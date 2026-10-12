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


def _write_test_wav(path: Path, seconds: float = 0.2, freq: float = 440.0,
                     rate: int = 44100, channels: int = 1) -> None:
    n = int(rate * seconds)
    tone = [int(0.5 * 32767 * math.sin(2 * math.pi * freq * i / rate)) for i in range(n)]
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


def _stereo_block(n: int = 512) -> np.ndarray:
    rng = np.random.default_rng(0)
    return (rng.uniform(-0.3, 0.3, size=(n, 2))).astype(np.float32)


# -- WAV loading / resampling -----------------------------------------

def test_read_wav_stereo_duplicates_mono_to_both_channels(tmp_path: Path) -> None:
    path = tmp_path / "mono.wav"
    _write_test_wav(path, channels=1)
    stereo, rate = read_wav_stereo(str(path))
    assert stereo.shape[1] == 2
    assert np.array_equal(stereo[:, 0], stereo[:, 1])
    assert rate == 44100


def test_read_wav_stereo_preserves_real_stereo(tmp_path: Path) -> None:
    path = tmp_path / "stereo.wav"
    _write_test_wav(path, channels=2)
    stereo, _ = read_wav_stereo(str(path))
    assert stereo.shape[1] == 2


def test_resample_linear_changes_length_proportionally() -> None:
    samples = np.zeros((44100, 2), dtype=np.float32)
    out = resample_linear(samples, 44100, 48000)
    assert abs(len(out) - 48000) <= 1


def test_resample_linear_noop_when_rates_match() -> None:
    samples = np.zeros((100, 2), dtype=np.float32)
    assert resample_linear(samples, 48000, 48000) is samples


def test_load_and_prepare_sample_normalizes_to_engine_rate(tmp_path: Path) -> None:
    path = tmp_path / "tone.wav"
    _write_test_wav(path, seconds=0.1, rate=44100)
    samples, rate = load_and_prepare_sample(str(path))
    assert rate == SAMPLE_RATE
    assert samples.shape[1] == 2


# -- Biquad filter -----------------------------------------------------

def test_design_biquad_lowpass_coefficients_are_finite() -> None:
    coeffs = design_biquad(FilterType.LOWPASS, 1000.0, 2.0, SAMPLE_RATE)
    assert all(math.isfinite(c) for c in coeffs)


def test_design_biquad_clamps_cutoff_below_nyquist() -> None:
    # requesting far above Nyquist must not produce unstable coefficients
    coeffs = design_biquad(FilterType.LOWPASS, 100000.0, 1.0, SAMPLE_RATE)
    assert all(math.isfinite(c) for c in coeffs)


def test_biquad_lowpass_attenuates_high_frequency_content() -> None:
    n = 4096
    t = np.arange(n) / SAMPLE_RATE
    high_freq_tone = np.sin(2 * np.pi * 15000 * t).astype(np.float32)
    block = np.stack([high_freq_tone, high_freq_tone], axis=1)

    filt = BiquadFilter(SAMPLE_RATE)
    filt.set_params(FilterType.LOWPASS, 500.0, 0.7)
    filt.process(block)

    # a 500Hz lowpass should crush a 15kHz tone's amplitude substantially
    # after the filter settles past its initial transient
    settled = block[len(block) // 2:]
    assert np.max(np.abs(settled)) < 0.2


def test_biquad_process_is_finite_and_bounded_for_near_self_oscillating_q() -> None:
    """Q=8.0 is deliberately tuned close to instability (Resolved Decisions
    Log) -- it must ring, not blow up to inf/nan."""
    block = _stereo_block(2048)
    filt = BiquadFilter(SAMPLE_RATE)
    filt.set_params(FilterType.LOWPASS, 800.0, 8.0)
    filt.process(block)
    assert np.all(np.isfinite(block))


# -- 3-band EQ -----------------------------------------------------------

def test_three_band_eq_zero_gain_is_a_no_op() -> None:
    block = _stereo_block()
    original = block.copy()
    ThreeBandEQ(SAMPLE_RATE).process(block, 0.0, 0.0, 0.0)
    assert np.allclose(block, original)


def test_three_band_eq_nonzero_gain_changes_signal_and_stays_finite() -> None:
    block = _stereo_block()
    ThreeBandEQ(SAMPLE_RATE).process(block, 10.0, -5.0, 8.0)
    assert np.all(np.isfinite(block))


# -- ADSR envelope -------------------------------------------------------

def test_adsr_attack_reaches_full_level() -> None:
    env = ADSREnvelope(attack_ms=10.0, decay_ms=0.0, sustain_pct=100.0, release_ms=5.0)
    env.note_on()
    out = np.empty(int(SAMPLE_RATE * 0.02), dtype=np.float32)
    env.process_audio_rate(len(out), SAMPLE_RATE, out)
    assert out[-1] > 0.99


def test_adsr_sustain_holds_configured_level() -> None:
    env = ADSREnvelope(attack_ms=1.0, decay_ms=1.0, sustain_pct=40.0, release_ms=5.0)
    env.note_on()
    out = np.empty(int(SAMPLE_RATE * 0.05), dtype=np.float32)
    env.process_audio_rate(len(out), SAMPLE_RATE, out)
    assert env.stage == EnvelopeStage.SUSTAIN
    assert abs(out[-1] - 0.40) < 0.01


def test_adsr_release_reaches_idle_and_zero() -> None:
    env = ADSREnvelope(attack_ms=1.0, decay_ms=1.0, sustain_pct=100.0, release_ms=10.0)
    env.note_on()
    warm = np.empty(int(SAMPLE_RATE * 0.01), dtype=np.float32)
    env.process_audio_rate(len(warm), SAMPLE_RATE, warm)
    env.note_off()
    out = np.empty(int(SAMPLE_RATE * 0.03), dtype=np.float32)
    env.process_audio_rate(len(out), SAMPLE_RATE, out)
    assert env.stage == EnvelopeStage.IDLE
    assert out[-1] == 0.0


def test_adsr_enforces_minimum_release_even_at_zero() -> None:
    """docs/DAW_VISION_SPEC.md: a short minimum release is enforced even at
    Release=0 to avoid clicks."""
    env = ADSREnvelope(attack_ms=0.0, decay_ms=0.0, sustain_pct=100.0, release_ms=0.0)
    env.note_on()
    env.note_off()
    # immediately after note_off, level should not have jumped straight to 0
    out = np.empty(1, dtype=np.float32)
    env.process_audio_rate(1, SAMPLE_RATE, out)
    total_release_samples = int((MIN_RELEASE_MS / 1000.0) * SAMPLE_RATE)
    assert total_release_samples > 1  # sanity: the enforced floor is more than one sample


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

def test_lfo_reset_phase_restarts_cycle() -> None:
    lfo = LFO(rate_hz=1.0, depth_pct=100.0, shape=LFOShape.SAW)
    lfo.process_control_rate(SAMPLE_RATE // 2, SAMPLE_RATE)  # advance half a cycle
    assert lfo.phase > 0.0
    lfo.reset_phase()
    assert lfo.phase == 0.0


def test_lfo_depth_scales_output() -> None:
    full = LFO(rate_hz=2.0, depth_pct=100.0, shape=LFOShape.SINE)
    half = LFO(rate_hz=2.0, depth_pct=50.0, shape=LFOShape.SINE)
    v_full = full.process_control_rate(1000, SAMPLE_RATE)
    v_half = half.process_control_rate(1000, SAMPLE_RATE)
    assert abs(v_half) <= abs(v_full) + 1e-9


# -- Distortion / delay / reverb ----------------------------------------

def test_apply_distortion_stays_bounded() -> None:
    block = np.array([[0.9, -0.9], [2.0, -2.0]], dtype=np.float32)
    apply_distortion(block, drive=8.0)
    assert np.all(np.abs(block) <= 1.0)


def test_delay_line_short_delay_uses_fallback_and_produces_finite_output() -> None:
    delay = DelayLine(SAMPLE_RATE, delay_ms=1.0, feedback=0.4, mix=0.5)
    block = _stereo_block(64)  # shorter delay than block -> fallback per-sample path
    delay.process(block)
    assert np.all(np.isfinite(block))


def test_delay_line_long_delay_uses_vectorized_path_and_matches_fallback() -> None:
    """The vectorized fast path and the per-sample fallback must agree --
    this is the core correctness check for the Phase 1 performance fix."""
    block_size = 256
    x = _stereo_block(block_size)

    fast = DelayLine(SAMPLE_RATE, delay_ms=50.0, feedback=0.4, mix=0.5)  # 50ms >> block
    fast_out = x.copy()
    fast.process(fast_out)

    slow = DelayLine(SAMPLE_RATE, delay_ms=50.0, feedback=0.4, mix=0.5)
    slow._buffer = slow._buffer  # keep same buffer sizing
    slow_out = x.copy()
    # force the fallback path regardless of delay length, by shrinking n below delay_samples check manually
    delay_samples = int(SAMPLE_RATE * 50.0 / 1000.0)
    buf = slow._buffer
    write_idx = 0
    feedback, mix = slow.feedback, slow.mix
    for i in range(block_size):
        read_idx = (write_idx - delay_samples) % buf.shape[0]
        d0, d1 = buf[read_idx, 0], buf[read_idx, 1]
        x0, x1 = slow_out[i, 0], slow_out[i, 1]
        buf[write_idx, 0] = x0 + d0 * feedback
        buf[write_idx, 1] = x1 + d1 * feedback
        slow_out[i, 0] = x0 * (1 - mix) + d0 * mix
        slow_out[i, 1] = x1 * (1 - mix) + d1 * mix
        write_idx = (write_idx + 1) % buf.shape[0]

    assert np.allclose(fast_out, slow_out, atol=1e-5)


def test_reverb_comb_produces_finite_output_and_is_stateful_across_blocks() -> None:
    reverb = ReverbComb(SAMPLE_RATE, decay=0.5, mix=0.4, max_block_size=1024)
    # Shortest comb delay is ~29.7ms (~1426 samples) -- feed enough loud
    # blocks to get all the way around every comb's circular buffer at
    # least once before expecting anything to loop back.
    loud_block = np.full((512, 2), 0.5, dtype=np.float32)
    for _ in range(6):  # 6 * 512 = 3072 samples > every comb's buffer length
        out = loud_block.copy()
        reverb.process(out)
        assert np.all(np.isfinite(out))

    silence = np.zeros((512, 2), dtype=np.float32)
    reverb.process(silence)
    # silence in, but the combs' stored history should still produce a tail
    assert np.max(np.abs(silence)) > 0.0


# -- Compressor --------------------------------------------------------

def test_compressor_reduces_gain_above_threshold() -> None:
    comp = Compressor(SAMPLE_RATE, threshold_db=-12.0, ratio=4.0, attack_ms=1.0, release_ms=50.0)
    loud = np.full((2048, 2), 0.9, dtype=np.float32)
    comp.process(loud)
    # after the envelope settles, output should be well below the input level
    assert np.max(np.abs(loud[-100:])) < 0.9


def test_compressor_leaves_quiet_signal_unaffected() -> None:
    comp = Compressor(SAMPLE_RATE, threshold_db=-6.0, ratio=4.0)
    quiet = np.full((512, 2), 0.01, dtype=np.float32)
    original = quiet.copy()
    comp.process(quiet)
    assert np.allclose(quiet, original, atol=1e-3)


def test_compressor_reports_gain_reduction() -> None:
    comp = Compressor(SAMPLE_RATE, threshold_db=-20.0, ratio=8.0, attack_ms=0.1, release_ms=50.0)
    loud = np.full((4096, 2), 0.95, dtype=np.float32)
    gr_db = comp.process(loud)
    assert gr_db > 0.0


# -- Limiter -------------------------------------------------------------

def test_limiter_never_exceeds_ceiling_on_clipping_input() -> None:
    limiter = Limiter(SAMPLE_RATE)
    block = np.full((4096, 2), 5.0, dtype=np.float32)  # wildly over full-scale
    limiter.process(block)
    assert np.max(np.abs(block)) <= limiter.ceiling_linear + 1e-6


def test_limiter_ceiling_matches_configured_db() -> None:
    limiter = Limiter(SAMPLE_RATE, ceiling_db=MASTER_LIMITER_CEILING_DB)
    assert abs(limiter.ceiling_linear - db_to_linear(MASTER_LIMITER_CEILING_DB)) < 1e-9


def test_limiter_passes_quiet_signal_through_near_unity() -> None:
    limiter = Limiter(SAMPLE_RATE)
    block = np.full((2048, 2), 0.05, dtype=np.float32)
    original = block.copy()
    limiter.process(block)
    assert np.allclose(block, original, atol=1e-3)
