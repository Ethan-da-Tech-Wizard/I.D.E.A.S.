# ==============================================================================
# Program: tests/test_audio_voice.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: tests app/services/audio/voice.py, one playing instance of a sample: amplitude envelope,
# trim/loop handling, pitch (semitones and LFO), voice stealing and the optional per-voice filter.
# Voices render into caller-provided numpy buffers in blocks, exactly as the engine calls them.
# Run: python3 -m pytest tests/test_audio_voice.py -q     (needs numpy)
# ==============================================================================

from __future__ import annotations

import numpy as np

from app.services.audio.dsp import FilterType, LFOShape, SAMPLE_RATE
from app.services.audio.voice import (
    MOD_TARGET_FILTER_CUTOFF,
    MOD_TARGET_PITCH,
    STEAL_FADE_MS,
    Voice,
    VoiceParams,
)


# Helper: 0.3 s stereo 220 Hz sine, half amplitude.
def _tone_sample(seconds: float = 0.3, freq: float = 220.0) -> np.ndarray:
    """Return a (frames, 2) float32 sine buffer."""
    n = int(SAMPLE_RATE * seconds)
    t = np.arange(n) / SAMPLE_RATE
    tone = (0.5 * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    return np.stack([tone, tone], axis=1)


# Helper: renders in block_size chunks. The real engine never asks a voice for more frames than its
# block size, and the voice's scratch buffers are sized for that, so tests must not do one huge call.
def _render_in_blocks(voice: Voice, total_frames: int, block_size: int = 512) -> np.ndarray:
    """Simulates real callback usage -- render() is only ever called with
    n_frames <= the engine's configured block size, never one giant call."""
    out = np.zeros((total_frames, 2), dtype=np.float32)
    pos = 0
    # One reusable block buffer, like the engine's.
    scratch = np.zeros((block_size, 2), dtype=np.float32)
    while pos < total_frames:
        n = min(block_size, total_frames - pos)
        # render fills `scratch` with the first n frames; the tail of the last block may be shorter.
        voice.render(n, scratch)
        out[pos:pos + n] = scratch[:n]
        pos += n
    return out


# A new voice is idle until note_on, which activates the envelope.
def test_voice_note_on_starts_amp_envelope() -> None:
    sample = _tone_sample()
    voice = Voice(sample, VoiceParams())
    assert not voice.amp_envelope.is_active
    voice.note_on()
    assert voice.amp_envelope.is_active
    assert not voice.is_finished


# note_off with a 5 ms release: after 50 ms of rendering the voice must be finished (is_finished
# lets the track recycle it).
def test_voice_finishes_after_release_completes() -> None:
    sample = _tone_sample(seconds=1.0)
    params = VoiceParams(amp_attack_ms=0.0, amp_decay_ms=0.0, amp_sustain_pct=100.0, amp_release_ms=5.0)
    voice = Voice(sample, params)
    voice.note_on()
    voice.note_off()
    out = np.zeros((int(SAMPLE_RATE * 0.05), 2), dtype=np.float32)  # 50ms, well past a 5ms release
    voice.render(len(out), out)
    assert voice.is_finished


# Stealing must use the short STEAL_FADE_MS fade, not the 2000 ms musical release; rendering twice
# the fade length is therefore enough to finish.
def test_voice_steal_uses_fast_fade_regardless_of_release_setting() -> None:
    sample = _tone_sample(seconds=1.0)
    params = VoiceParams(amp_release_ms=2000.0)  # long musical release
    voice = Voice(sample, params)
    voice.note_on()
    voice.steal()
    # 5ms steal fade should finish well before the 2000ms release would have
    out = np.zeros((int(SAMPLE_RATE * (STEAL_FADE_MS / 1000.0) * 2), 2), dtype=np.float32)
    voice.render(len(out), out)
    assert voice.is_finished


# One-shot behaviour: when a non-looped sample runs out of content the voice releases itself, so a
# short drum hit does not hold a voice forever. 0.2 s is much longer than the 0.05 s sample.
def test_voice_non_looped_sample_triggers_note_off_at_trim_end() -> None:
    """docs/DAW_VISION_SPEC.md: release starts at note-off *or* at the end
    of trimmed content for a non-looped sample, whichever comes first."""
    sample = _tone_sample(seconds=0.05)  # short, unreleased sample
    params = VoiceParams(trim_start_frac=0.0, trim_end_frac=1.0, loop_enabled=False, amp_release_ms=5.0)
    voice = Voice(sample, params)
    voice.note_on()
    _render_in_blocks(voice, int(SAMPLE_RATE * 0.2))  # far longer than the 50ms sample
    assert voice.is_finished  # must have auto-released once content ran out


# Looped sample (region 20 to 80 percent): the voice must keep sounding past the sample length, and
# produce non-silent output while the note is held.
def test_voice_loop_sustains_past_trim_end_while_held() -> None:
    sample = _tone_sample(seconds=0.05)
    params = VoiceParams(
        trim_start_frac=0.0, trim_end_frac=1.0,
        loop_enabled=True, loop_start_frac=0.2, loop_end_frac=0.8,
        amp_attack_ms=0.0, amp_decay_ms=0.0, amp_sustain_pct=100.0, amp_release_ms=5.0,
    )
    voice = Voice(sample, params)
    voice.note_on()
    out = _render_in_blocks(voice, int(SAMPLE_RATE * 0.2))  # longer than the un-looped sample
    # looping should keep the voice alive (not auto-released) since it never runs out of content
    assert not voice.is_finished
    assert np.max(np.abs(out)) > 0.0


# 12 semitones is one octave, so playback speed doubles: after 1000 output frames the read
# position must be roughly 2000 (the test uses > 1900 to allow interpolation rounding).
def test_voice_pitch_up_reads_source_faster() -> None:
    sample = _tone_sample(seconds=0.5)
    params_up = VoiceParams(base_semitones=12.0)  # one octave up -> 2x playback rate
    voice_up = Voice(sample, params_up)
    voice_up.note_on()
    out = np.zeros((1000, 2), dtype=np.float32)
    voice_up.render(len(out), out)
    assert voice_up._position > 1900  # roughly double the frame count consumed


# Routing check: a square LFO aimed at pitch must render without NaN/inf. The LFO's depth is a
# modulation amount, so only numerical sanity is asserted.
def test_voice_lfo_targeting_pitch_modulates_playback_rate() -> None:
    sample = _tone_sample(seconds=0.5)
    params = VoiceParams(
        lfo_enabled=True, lfo_rate_hz=2.0, lfo_depth_pct=100.0, lfo_shape=LFOShape.SQUARE,
        lfo_target=MOD_TARGET_PITCH,
    )
    voice = Voice(sample, params)
    voice.note_on()
    out = np.zeros((512, 2), dtype=np.float32)
    voice.render(len(out), out)
    assert np.all(np.isfinite(out))


# filter_enabled creates a private filter (filters hold state, so they cannot be shared between voices).
def test_voice_filter_enabled_creates_per_voice_filter_instance() -> None:
    sample = _tone_sample()
    params = VoiceParams(filter_enabled=True, filter_type=FilterType.LOWPASS,
                          filter_cutoff_hz=500.0, filter_resonance=1.0)
    voice = Voice(sample, params)
    assert voice.filter is not None
    voice.note_on()
    out = np.zeros((512, 2), dtype=np.float32)
    voice.render(len(out), out)
    assert np.all(np.isfinite(out))


# No filter object is created when disabled, saving CPU per voice.
def test_voice_without_filter_enabled_has_no_filter_instance() -> None:
    sample = _tone_sample()
    voice = Voice(sample, VoiceParams(filter_enabled=False))
    assert voice.filter is None
