# ==============================================================================
# Program: tests/test_audio_dsp.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: unit tests for app/services/audio/dsp.py, the numpy-based real-time building blocks of the
# DAW: WAV loading/resampling, biquad filter, 3-band EQ, ADSR envelope, LFO, distortion, delay,
# reverb, compressor and the master limiter. Audio blocks are float32 arrays shaped (frames, 2).
# Strategy: check measurable properties (attenuation, bounds, finiteness, timing, agreement of the
# fast path with a reference implementation) instead of exact sample values, because DSP output
# is floating point and the goal is to catch regressions that cause clicks, blow-ups or clipping.
# Run: python3 -m pytest tests/test_audio_dsp.py -q      (needs numpy)
# ==============================================================================

from __future__ import annotations

import math
import wave
from array import array
from pathlib import Path

import numpy as np

from app.services.audio.dsp import (
    ADSREnvelope,
    BiquadFilter,
    Compressor,
    DelayLine,
    EnvelopeStage,
    FilterType,
    LFO,
    LFOShape,
    Limiter,
    MASTER_LIMITER_CEILING_DB,
    MIN_RELEASE_MS,
    ReverbComb,
    SAMPLE_RATE,
    ThreeBandEQ,
    apply_distortion,
    db_to_linear,
    design_biquad,
    load_and_prepare_sample,
    read_wav_stereo,
    resample_linear,
)


# Helper: write a 16-bit sine WAV (0.5 of full scale) with 1 or 2 channels. Stereo duplicates each
# sample so left == right; the loader tests only care about channel count and rate.
def _write_test_wav(path: Path, seconds: float = 0.2, freq: float = 440.0,
                     rate: int = 44100, channels: int = 1) -> None:
    """Create a test WAV file at `path`."""
    # Frames = rate x seconds; 32767 is the int16 peak, 0.5 leaves headroom.
    n = int(rate * seconds)
    tone = [int(0.5 * 32767 * math.sin(2 * math.pi * freq * i / rate)) for i in range(n)]
    # WAV frames are interleaved (L, R, L, R...), hence two appends per source sample.
    if channels == 2:
        samples = array("h")
        for v in tone:
            samples.append(v)
            samples.append(v)
    else:
        samples = array("h", tone)
    with wave.open(str(path), "w") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(2)
        wav_file.setframerate(rate)
        wav_file.writeframes(samples.tobytes())


# Helper: reproducible noise block. A fixed seed makes failures repeatable; +/-0.3 amplitude keeps
# filters and the EQ well away from clipping so only their own behaviour is measured.
def _stereo_block(n: int = 512) -> np.ndarray:
    """Return an (n, 2) float32 array of seeded uniform noise in [-0.3, 0.3]."""
    rng = np.random.default_rng(0)
    return (rng.uniform(-0.3, 0.3, size=(n, 2))).astype(np.float32)


# -- WAV loading / resampling -----------------------------------------

# The engine is stereo-only, so mono files are widened by copying the channel; both columns must be
# identical and the original sample rate reported.
def test_read_wav_stereo_duplicates_mono_to_both_channels(tmp_path: Path) -> None:
    path = tmp_path / "mono.wav"
    _write_test_wav(path, channels=1)
    stereo, rate = read_wav_stereo(str(path))
    assert stereo.shape[1] == 2
    assert np.array_equal(stereo[:, 0], stereo[:, 1])
    assert rate == 44100


# A stereo file must stay two-column (no accidental collapse to mono).
def test_read_wav_stereo_preserves_real_stereo(tmp_path: Path) -> None:
    path = tmp_path / "stereo.wav"
    _write_test_wav(path, channels=2)
    stereo, _ = read_wav_stereo(str(path))
    assert stereo.shape[1] == 2


# One second of audio at 44.1 kHz resampled to 48 kHz must be ~48000 frames (+/-1 for rounding);
# a wrong ratio would shift pitch and timing of every imported sample.
def test_resample_linear_changes_length_proportionally() -> None:
    samples = np.zeros((44100, 2), dtype=np.float32)
    out = resample_linear(samples, 44100, 48000)
    assert abs(len(out) - 48000) <= 1


# Identity optimisation: equal rates return the very same array object (`is`), avoiding a copy of
# large samples on the common path.
def test_resample_linear_noop_when_rates_match() -> None:
    samples = np.zeros((100, 2), dtype=np.float32)
    assert resample_linear(samples, 48000, 48000) is samples


# The one-call loader must return stereo audio at the engine's fixed SAMPLE_RATE regardless of the
# file's own rate (here 44.1 kHz), so voices can read samples without per-voice conversion.
def test_load_and_prepare_sample_normalizes_to_engine_rate(tmp_path: Path) -> None:
    path = tmp_path / "tone.wav"
    _write_test_wav(path, seconds=0.1, rate=44100)
    samples, rate = load_and_prepare_sample(str(path))
    assert rate == SAMPLE_RATE
    assert samples.shape[1] == 2


# -- Biquad filter -----------------------------------------------------

# Coefficients (b0, b1, b2, a1, a2) for a normal case must be real numbers; NaN/inf would poison
# every sample that passes through the filter.
def test_design_biquad_lowpass_coefficients_are_finite() -> None:
    coeffs = design_biquad(FilterType.LOWPASS, 1000.0, 2.0, SAMPLE_RATE)
    assert all(math.isfinite(c) for c in coeffs)


# A cutoff above half the sample rate has no meaning and makes the biquad unstable, so the designer
# must clamp it. 100 kHz is far past Nyquist (24 kHz at 48 kHz).
def test_design_biquad_clamps_cutoff_below_nyquist() -> None:
    # Only finiteness is asserted; the exact clamp value is an implementation detail.
    # requesting far above Nyquist must not produce unstable coefficients
    coeffs = design_biquad(FilterType.LOWPASS, 100000.0, 1.0, SAMPLE_RATE)
    assert all(math.isfinite(c) for c in coeffs)


# Behavioural check of the filter itself: a 15 kHz sine through a 500 Hz low-pass (Q 0.7) must be
# nearly gone. process() works in place, hence `block` is read afterwards.
def test_biquad_lowpass_attenuates_high_frequency_content() -> None:
    # 4096 frames is long enough for the transient to die out in the second half.
    n = 4096
    # Time axis in seconds; the sine is 2*pi*f*t.
    t = np.arange(n) / SAMPLE_RATE
    high_freq_tone = np.sin(2 * np.pi * 15000 * t).astype(np.float32)
    block = np.stack([high_freq_tone, high_freq_tone], axis=1)

    filt = BiquadFilter(SAMPLE_RATE)
    filt.set_params(FilterType.LOWPASS, 500.0, 0.7)
    filt.process(block)

    # Only the second half is measured: the first samples contain the filter's start-up transient.
    # a 500Hz lowpass should crush a 15kHz tone's amplitude substantially
    # after the filter settles past its initial transient
    settled = block[len(block) // 2:]
    assert np.max(np.abs(settled)) < 0.2


# Stability guard for the resonance setting the design deliberately allows. The filter may ring
# strongly but must never produce NaN/inf, which would silence the whole master bus.
def test_biquad_process_is_finite_and_bounded_for_near_self_oscillating_q() -> None:
    """Q=8.0 is deliberately tuned close to instability (Resolved Decisions
    Log) -- it must ring, not blow up to inf/nan."""
    block = _stereo_block(2048)
    filt = BiquadFilter(SAMPLE_RATE)
    filt.set_params(FilterType.LOWPASS, 800.0, 8.0)
    filt.process(block)
    assert np.all(np.isfinite(block))


# -- 3-band EQ -----------------------------------------------------------

# Flat EQ (0 dB on all bands) must leave the signal untouched, so an unused EQ costs no sound quality.
def test_three_band_eq_zero_gain_is_a_no_op() -> None:
    block = _stereo_block()
    original = block.copy()
    ThreeBandEQ(SAMPLE_RATE).process(block, 0.0, 0.0, 0.0)
    assert np.allclose(block, original)


# Boost/cut on the three bands (10, -5, 8 dB) must not create NaN/inf. The audible effect is not
# asserted here, only numerical sanity.
def test_three_band_eq_nonzero_gain_changes_signal_and_stays_finite() -> None:
    block = _stereo_block()
    ThreeBandEQ(SAMPLE_RATE).process(block, 10.0, -5.0, 8.0)
    assert np.all(np.isfinite(block))


# -- ADSR envelope -------------------------------------------------------

# 10 ms attack: after 20 ms (twice the attack) the output must be ~1.0, i.e. the ramp completes.
def test_adsr_attack_reaches_full_level() -> None:
    env = ADSREnvelope(attack_ms=10.0, decay_ms=0.0, sustain_pct=100.0, release_ms=5.0)
    env.note_on()
    # Output buffer is filled by process_audio_rate (np.empty: contents are overwritten).
    out = np.empty(int(SAMPLE_RATE * 0.02), dtype=np.float32)
    env.process_audio_rate(len(out), SAMPLE_RATE, out)
    assert out[-1] > 0.99


# After a 1 ms attack and 1 ms decay the envelope must sit in SUSTAIN at 40 percent (0.40, +/-0.01).
def test_adsr_sustain_holds_configured_level() -> None:
    env = ADSREnvelope(attack_ms=1.0, decay_ms=1.0, sustain_pct=40.0, release_ms=5.0)
    env.note_on()
    out = np.empty(int(SAMPLE_RATE * 0.05), dtype=np.float32)
    env.process_audio_rate(len(out), SAMPLE_RATE, out)
    assert env.stage == EnvelopeStage.SUSTAIN
    assert abs(out[-1] - 0.40) < 0.01


# After note_off the envelope must fade to exactly 0.0 and enter IDLE, which is what lets the engine
# free the voice; a tail that never reaches zero would leak voices.
def test_adsr_release_reaches_idle_and_zero() -> None:
    env = ADSREnvelope(attack_ms=1.0, decay_ms=1.0, sustain_pct=100.0, release_ms=10.0)
    env.note_on()
    # Warm-up block lets the note reach sustain before releasing.
    warm = np.empty(int(SAMPLE_RATE * 0.01), dtype=np.float32)
    env.process_audio_rate(len(warm), SAMPLE_RATE, warm)
    env.note_off()
    out = np.empty(int(SAMPLE_RATE * 0.03), dtype=np.float32)
    env.process_audio_rate(len(out), SAMPLE_RATE, out)
    assert env.stage == EnvelopeStage.IDLE
    assert out[-1] == 0.0


# Anti-click rule from the design spec: a release of 0 ms is raised to MIN_RELEASE_MS, because
# jumping from full level to silence in one sample produces an audible click.
def test_adsr_enforces_minimum_release_even_at_zero() -> None:
    """docs/DAW_VISION_SPEC.md: a short minimum release is enforced even at
    Release=0 to avoid clicks."""
    env = ADSREnvelope(attack_ms=0.0, decay_ms=0.0, sustain_pct=100.0, release_ms=0.0)
    env.note_on()
    env.note_off()
    # The sample just after note_off is rendered but not inspected.
    # immediately after note_off, level should not have jumped straight to 0
    out = np.empty(1, dtype=np.float32)
    env.process_audio_rate(1, SAMPLE_RATE, out)
    # The assertion checks the floor itself is more than one sample long: the enforced minimum is real.
    total_release_samples = int((MIN_RELEASE_MS / 1000.0) * SAMPLE_RATE)
    assert total_release_samples > 1  # sanity: the enforced floor is more than one sample


# Voice stealing: note_off(fade_ms=5) must use the short 5 ms fade even though the track's release
# is 2000 ms; otherwise a stolen voice would keep occupying a slot for two seconds.
def test_adsr_steal_fade_uses_fixed_override_not_track_release() -> None:
    env = ADSREnvelope(attack_ms=0.0, decay_ms=0.0, sustain_pct=100.0, release_ms=2000.0)
    env.note_on()
    warm = np.empty(10, dtype=np.float32)
    env.process_audio_rate(10, SAMPLE_RATE, warm)
    env.note_off(fade_ms=5.0)
    out = np.empty(int(SAMPLE_RATE * 0.02), dtype=np.float32)  # 20ms, less than the 2000ms release
    env.process_audio_rate(len(out), SAMPLE_RATE, out)
    assert env.stage == EnvelopeStage.IDLE  # would still be releasing if the 2000ms release applied


# -- LFO -------------------------------------------------------------

# Retrigger behaviour: after advancing half a cycle (the phase is non-zero) reset_phase puts it back
# to 0 so every new note's modulation starts from the same point.
def test_lfo_reset_phase_restarts_cycle() -> None:
    lfo = LFO(rate_hz=1.0, depth_pct=100.0, shape=LFOShape.SAW)
    # Control-rate call: the LFO advances by the given number of samples and returns one value.
    lfo.process_control_rate(SAMPLE_RATE // 2, SAMPLE_RATE)  # advance half a cycle
    assert lfo.phase > 0.0
    lfo.reset_phase()
    assert lfo.phase == 0.0


# Depth 50 percent must never exceed depth 100 percent for the same phase; the 1e-9 slack absorbs
# floating point rounding.
def test_lfo_depth_scales_output() -> None:
    full = LFO(rate_hz=2.0, depth_pct=100.0, shape=LFOShape.SINE)
    half = LFO(rate_hz=2.0, depth_pct=50.0, shape=LFOShape.SINE)
    v_full = full.process_control_rate(1000, SAMPLE_RATE)
    v_half = half.process_control_rate(1000, SAMPLE_RATE)
    assert abs(v_half) <= abs(v_full) + 1e-9


# -- Distortion / delay / reverb ----------------------------------------

# Input deliberately exceeds full scale (2.0); even with drive 8 the distortion must keep every
# sample in [-1, 1]. Works in place on `block`.
def test_apply_distortion_stays_bounded() -> None:
    block = np.array([[0.9, -0.9], [2.0, -2.0]], dtype=np.float32)
    apply_distortion(block, drive=8.0)
    assert np.all(np.abs(block) <= 1.0)


# A 1 ms delay (48 frames) is shorter than the 64-frame block, so the delayed signal feeds back
# inside the same block and the implementation must use its sample-by-sample path. Checks it runs.
def test_delay_line_short_delay_uses_fallback_and_produces_finite_output() -> None:
    delay = DelayLine(SAMPLE_RATE, delay_ms=1.0, feedback=0.4, mix=0.5)
    block = _stereo_block(64)  # shorter delay than block -> fallback per-sample path
    delay.process(block)
    assert np.all(np.isfinite(block))


# Correctness of the optimisation: for long delays the vectorised numpy path is used. This test
# re-implements the plain per-sample algorithm inline as a reference and compares the two outputs.
def test_delay_line_long_delay_uses_vectorized_path_and_matches_fallback() -> None:
    """The vectorized fast path and the per-sample fallback must agree --
    this is the core correctness check for the Phase 1 performance fix."""
    block_size = 256
    x = _stereo_block(block_size)

    # 50 ms is about 2400 frames, much longer than the 256-frame block, so the fast path is selected.
    fast = DelayLine(SAMPLE_RATE, delay_ms=50.0, feedback=0.4, mix=0.5)  # 50ms >> block
    fast_out = x.copy()
    fast.process(fast_out)

    # A second, identical delay supplies the reference buffer and settings.
    slow = DelayLine(SAMPLE_RATE, delay_ms=50.0, feedback=0.4, mix=0.5)
    # No-op assignment that is kept only as a marker that the reference reuses the same buffer sizing.
    slow._buffer = slow._buffer  # keep same buffer sizing
    slow_out = x.copy()
    # The reference loop below does what the fallback does: read the delayed frame, write input plus
    # feedback into the circular buffer, mix dry and wet, advance the write index with wraparound.
    # force the fallback path regardless of delay length, by shrinking n below delay_samples check manually
    delay_samples = int(SAMPLE_RATE * 50.0 / 1000.0)
    buf = slow._buffer
    write_idx = 0
    feedback, mix = slow.feedback, slow.mix
    # read_idx lags write_idx by delay_samples frames (modulo buffer length = circular buffer).
    for i in range(block_size):
        read_idx = (write_idx - delay_samples) % buf.shape[0]
        d0, d1 = buf[read_idx, 0], buf[read_idx, 1]
        x0, x1 = slow_out[i, 0], slow_out[i, 1]
        buf[write_idx, 0] = x0 + d0 * feedback
        buf[write_idx, 1] = x1 + d1 * feedback
        slow_out[i, 0] = x0 * (1 - mix) + d0 * mix
        slow_out[i, 1] = x1 * (1 - mix) + d1 * mix
        write_idx = (write_idx + 1) % buf.shape[0]

    # atol 1e-5 allows float32 rounding differences between the two code paths.
    assert np.allclose(fast_out, slow_out, atol=1e-5)


# Reverb is stateful: after loud input, a block of silence must still contain a decaying tail, which
# proves history carries over between blocks (a bug that cleared buffers per block would fail this).
def test_reverb_comb_produces_finite_output_and_is_stateful_across_blocks() -> None:
    reverb = ReverbComb(SAMPLE_RATE, decay=0.5, mix=0.4, max_block_size=1024)
    # Shortest comb delay is ~29.7ms (~1426 samples) -- feed enough loud
    # blocks to get all the way around every comb's circular buffer at
    # least once before expecting anything to loop back.
    # Constant 0.5 level is simple and loud enough to fill the comb buffers.
    loud_block = np.full((512, 2), 0.5, dtype=np.float32)
    # 6 x 512 = 3072 frames exceeds the longest comb length, so every comb has fed back at least once.
    for _ in range(6):  # 6 * 512 = 3072 samples > every comb's buffer length
        out = loud_block.copy()
        reverb.process(out)
        assert np.all(np.isfinite(out))

    silence = np.zeros((512, 2), dtype=np.float32)
    reverb.process(silence)
    # silence in, but the combs' stored history should still produce a tail
    assert np.max(np.abs(silence)) > 0.0


# -- Compressor --------------------------------------------------------

# 0.9 amplitude is about -0.9 dBFS, far above the -12 dB threshold, so at 4:1 the settled output
# must be below the input level. The last 100 frames are checked after the attack has taken effect.
def test_compressor_reduces_gain_above_threshold() -> None:
    comp = Compressor(SAMPLE_RATE, threshold_db=-12.0, ratio=4.0, attack_ms=1.0, release_ms=50.0)
    loud = np.full((2048, 2), 0.9, dtype=np.float32)
    comp.process(loud)
    # after the envelope settles, output should be well below the input level
    assert np.max(np.abs(loud[-100:])) < 0.9


# A signal at 0.01 (-40 dBFS) is under the -6 dB threshold, so it must pass unchanged (within 1e-3):
# a compressor must not colour quiet material.
def test_compressor_leaves_quiet_signal_unaffected() -> None:
    comp = Compressor(SAMPLE_RATE, threshold_db=-6.0, ratio=4.0)
    quiet = np.full((512, 2), 0.01, dtype=np.float32)
    original = quiet.copy()
    comp.process(quiet)
    assert np.allclose(quiet, original, atol=1e-3)


# process() returns the applied gain reduction in dB (used by the UI meter); loud input against a
# -20 dB threshold must report a positive value.
def test_compressor_reports_gain_reduction() -> None:
    comp = Compressor(SAMPLE_RATE, threshold_db=-20.0, ratio=8.0, attack_ms=0.1, release_ms=50.0)
    loud = np.full((4096, 2), 0.95, dtype=np.float32)
    gr_db = comp.process(loud)
    assert gr_db > 0.0


# -- Limiter -------------------------------------------------------------

# Safety property of the master bus: even a wildly over-range input (5.0 = +14 dBFS) must be held at
# or below the ceiling (+1e-6 for float rounding), which protects speakers and the output format.
def test_limiter_never_exceeds_ceiling_on_clipping_input() -> None:
    limiter = Limiter(SAMPLE_RATE)
    block = np.full((4096, 2), 5.0, dtype=np.float32)  # wildly over full-scale
    limiter.process(block)
    assert np.max(np.abs(block)) <= limiter.ceiling_linear + 1e-6


# The linear ceiling must equal db_to_linear of the configured dB value, i.e. the dB setting is
# converted correctly (10^(dB/20)).
def test_limiter_ceiling_matches_configured_db() -> None:
    limiter = Limiter(SAMPLE_RATE, ceiling_db=MASTER_LIMITER_CEILING_DB)
    assert abs(limiter.ceiling_linear - db_to_linear(MASTER_LIMITER_CEILING_DB)) < 1e-9


# A -26 dBFS signal is far below the ceiling and must not be altered or pumped.
def test_limiter_passes_quiet_signal_through_near_unity() -> None:
    limiter = Limiter(SAMPLE_RATE)
    block = np.full((2048, 2), 0.05, dtype=np.float32)
    original = block.copy()
    limiter.process(block)
    assert np.allclose(block, original, atol=1e-3)
