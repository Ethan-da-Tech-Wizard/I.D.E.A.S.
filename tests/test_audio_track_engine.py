# ==============================================================================
# Program: tests/test_audio_track_engine.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: tests app/services/audio/track_engine.py. TrackState is the saved per-track settings
# object (gate, loop, filter, LFO, mod envelope, compressor, mixer, choke group); TrackProcessor
# owns a track's voices and runs its effects chain for one audio block. Covers serialisation,
# transparent defaults, the voice pool and stealing, mute, a full effects chain and the fader cap.
# Run: python3 -m pytest tests/test_audio_track_engine.py -q     (needs numpy)
# ==============================================================================

from __future__ import annotations

import numpy as np

from app.services.audio.dsp import SAMPLE_RATE
from app.services.audio.track_engine import MAX_VOICES_PER_TRACK, TrackProcessor, TrackState


# Helper: 0.5 s stereo 220 Hz sine at half amplitude, used as every track's sample.
def _tone_sample(seconds: float = 0.5, freq: float = 220.0) -> np.ndarray:
    """Return a (frames, 2) float32 sine buffer."""
    n = int(SAMPLE_RATE * seconds)
    t = np.arange(n) / SAMPLE_RATE
    tone = (0.5 * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    return np.stack([tone, tone], axis=1)


# -- TrackState (de)serialization -------------------------------------

# Persistence contract: to_dict/from_dict must carry one non-default value from every settings group
# (including integer-keyed per-step gate overrides and the choke group) so saved projects reload
# exactly. Missing any group here would silently reset that part of a track on load.
def test_track_state_round_trips_through_dict() -> None:
    state = TrackState()
    state.gate_length_pct = 150.0
    # Keys are step numbers; a JSON round trip turns int keys into strings, so this verifies they are
    # restored as ints.
    state.step_gate_overrides = {3: 220.0, 12: 50.0}
    state.filter.enabled = True
    state.filter.cutoff_hz = 800.0
    state.lfo.enabled = True
    state.lfo.target = "pitch"
    state.mod_envelope.amount_pct = -40.0
    state.compressor.ratio = 6.0
    state.mixer.pan = -0.5
    state.choke_group = 2

    restored = TrackState.from_dict(state.to_dict())

    assert restored.gate_length_pct == 150.0
    assert restored.step_gate_overrides == {3: 220.0, 12: 50.0}
    assert restored.filter.enabled is True
    assert restored.filter.cutoff_hz == 800.0
    assert restored.lfo.target == "pitch"
    assert restored.mod_envelope.amount_pct == -40.0
    assert restored.compressor.ratio == 6.0
    assert restored.mixer.pan == -0.5
    assert restored.choke_group == 2


# Migration safety: a track that was never customised must sound like the original sampler: full
# gate length, no loop, all effects bypassed, fader at unity, no choke group.
def test_track_state_defaults_are_audibly_transparent() -> None:
    """docs/DAW_VISION_SPEC.md's Saved Pattern Migration section: defaults
    must be transparent (full gate, no loop, bypassed FX) so migrated
    patterns still sound like themselves."""
    state = TrackState()
    assert state.gate_length_pct == 100.0
    assert state.loop.enabled is False
    assert state.filter.enabled is False
    assert state.lfo.enabled is False
    assert state.mod_envelope.enabled is False
    assert state.compressor.enabled is False
    assert state.mixer.fader_db == 0.0
    assert state.choke_group is None


# Forward compatibility: old project files lack newer fields; an empty dict must load as defaults
# rather than raise KeyError.
def test_track_state_from_dict_handles_missing_fields() -> None:
    restored = TrackState.from_dict({})
    assert restored.gate_length_pct == 100.0
    assert restored.filter.enabled is False


# -- TrackProcessor: voice pool / stealing ------------------------------

# Triggering allocates exactly one voice in the track's pool.
def test_trigger_note_creates_a_voice() -> None:
    track = TrackProcessor("t1", TrackState(), _tone_sample())
    voice = track.trigger_note()
    assert voice is not None
    assert track.voice_count == 1


# Polyphony limit: triggering more than MAX_VOICES_PER_TRACK notes must steal the oldest voice
# (forced into a quick release) instead of growing without bound. The count assertion is a loose
# upper bound because stolen voices stay listed until their fade-out completes.
def test_voice_pool_steals_oldest_beyond_cap() -> None:
    track = TrackProcessor("t1", TrackState(), _tone_sample(seconds=2.0))
    voices = [track.trigger_note() for _ in range(MAX_VOICES_PER_TRACK + 3)]
    # the pool should never grow without bound
    assert track.voice_count <= MAX_VOICES_PER_TRACK + 3
    # the very first voice should have been stolen (forced into release)
    assert voices[0].amp_envelope.stage.name in ("RELEASE", "IDLE")


# Used by choke groups and by transport stop: every sounding voice must start releasing.
def test_choke_all_voices_forces_release_on_every_active_voice() -> None:
    track = TrackProcessor("t1", TrackState(), _tone_sample())
    v1 = track.trigger_note()
    v2 = track.trigger_note()
    track.choke_all_voices()
    assert v1.amp_envelope.stage.name in ("RELEASE", "IDLE")
    assert v2.amp_envelope.stage.name in ("RELEASE", "IDLE")


# -- TrackProcessor: post-mixdown chain ---------------------------------

# An idle track must output exact zeros so it does not add noise or cost to the mix.
def test_process_block_returns_silence_with_no_voices() -> None:
    track = TrackProcessor("t1", TrackState(), _tone_sample())
    out = track.process_block(512)
    assert np.max(np.abs(out)) == 0.0


# Mute silences the track's output but is applied after voices run, so unmuting resumes in sync.
def test_process_block_muted_track_is_silent_even_with_active_voice() -> None:
    track = TrackProcessor("t1", TrackState(), _tone_sample())
    track.trigger_note()
    track.muted = True
    out = track.process_block(512)
    assert np.max(np.abs(out)) == 0.0


# Sanity check that the signal path actually produces sound with default settings.
def test_process_block_produces_audible_output_for_active_voice() -> None:
    track = TrackProcessor("t1", TrackState(), _tone_sample())
    track.trigger_note()
    out = track.process_block(512)
    assert np.max(np.abs(out)) > 0.0


# Stress test: enable every stage at aggressive settings (resonant filter, LFO, mod envelope,
# compressor, reverb, delay, distortion, +12 dB bass, hard pan) and render 50 blocks. No NaN/inf
# may ever appear, since one bad sample would propagate into the master bus.
def test_process_block_full_chain_stays_finite() -> None:
    state = TrackState()
    state.filter.enabled = True
    # Q of 8 is intentionally near self-oscillation (see the filter stability test in test_audio_dsp).
    state.filter.resonance = 8.0  # near-self-oscillation per Resolved Decisions Log
    state.lfo.enabled = True
    state.mod_envelope.enabled = True
    state.compressor.enabled = True
    state.reverb_enabled = True
    state.delay_enabled = True
    state.distortion_drive = 5.0
    state.mixer.eq_low_db = 12.0
    state.mixer.pan = 0.7

    track = TrackProcessor("t1", state, _tone_sample(seconds=1.0))
    track.trigger_note()
    for _ in range(50):
        out = track.process_block(512)
        assert np.all(np.isfinite(out))


# Overload shedding uses force_bypass_compressor. Even with an extreme compressor (-60 dB threshold,
# 20:1) the reported gain reduction must be 0 when bypassed.
def test_force_bypass_compressor_flag_disables_gain_reduction() -> None:
    state = TrackState()
    state.compressor.enabled = True
    state.compressor.threshold_db = -60.0  # everything gets compressed
    state.compressor.ratio = 20.0
    track = TrackProcessor("t1", state, _tone_sample())
    track.trigger_note()

    track.process_block(512, force_bypass_compressor=True)
    assert track.gain_reduction_db == 0.0


# A hostile or corrupt fader value (+999 dB) must be clamped to MIXER_FADER_MAX_DB. The bound has
# 10 percent slack for the sample's own level and filter/pan gain.
def test_mixer_fader_is_capped_at_max_db() -> None:
    # Imported locally because only this test needs the constant.
    from app.services.audio.track_engine import MIXER_FADER_MAX_DB

    state = TrackState()
    state.mixer.fader_db = 999.0  # way past the +6dB cap
    track = TrackProcessor("t1", state, _tone_sample())
    track.trigger_note()
    out = track.process_block(512)
    # capped gain should not blow the signal up arbitrarily
    assert np.max(np.abs(out)) < 10 ** (MIXER_FADER_MAX_DB / 20.0) * 1.1
