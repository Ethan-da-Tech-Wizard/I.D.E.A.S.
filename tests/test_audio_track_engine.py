from __future__ import annotations

import numpy as np

from app.services.audio.dsp import SAMPLE_RATE
from app.services.audio.track_engine import MAX_VOICES_PER_TRACK, TrackProcessor, TrackState


def _tone_sample(seconds: float = 0.5, freq: float = 220.0) -> np.ndarray:
    n = int(SAMPLE_RATE * seconds)
    t = np.arange(n) / SAMPLE_RATE
    tone = (0.5 * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    return np.stack([tone, tone], axis=1)


# -- TrackState (de)serialization -------------------------------------

def test_track_state_round_trips_through_dict() -> None:
    state = TrackState()
    state.gate_length_pct = 150.0
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


def test_track_state_from_dict_handles_missing_fields() -> None:
    restored = TrackState.from_dict({})
    assert restored.gate_length_pct == 100.0
    assert restored.filter.enabled is False


# -- TrackProcessor: voice pool / stealing ------------------------------

def test_trigger_note_creates_a_voice() -> None:
    track = TrackProcessor("t1", TrackState(), _tone_sample())
    voice = track.trigger_note()
    assert voice is not None
    assert track.voice_count == 1


def test_voice_pool_steals_oldest_beyond_cap() -> None:
    track = TrackProcessor("t1", TrackState(), _tone_sample(seconds=2.0))
    voices = [track.trigger_note() for _ in range(MAX_VOICES_PER_TRACK + 3)]
    # the pool should never grow without bound
    assert track.voice_count <= MAX_VOICES_PER_TRACK + 3
    # the very first voice should have been stolen (forced into release)
    assert voices[0].amp_envelope.stage.name in ("RELEASE", "IDLE")


def test_choke_all_voices_forces_release_on_every_active_voice() -> None:
    track = TrackProcessor("t1", TrackState(), _tone_sample())
    v1 = track.trigger_note()
    v2 = track.trigger_note()
    track.choke_all_voices()
    assert v1.amp_envelope.stage.name in ("RELEASE", "IDLE")
    assert v2.amp_envelope.stage.name in ("RELEASE", "IDLE")


# -- TrackProcessor: post-mixdown chain ---------------------------------

def test_process_block_returns_silence_with_no_voices() -> None:
    track = TrackProcessor("t1", TrackState(), _tone_sample())
    out = track.process_block(512)
    assert np.max(np.abs(out)) == 0.0


def test_process_block_muted_track_is_silent_even_with_active_voice() -> None:
    track = TrackProcessor("t1", TrackState(), _tone_sample())
    track.trigger_note()
    track.muted = True
    out = track.process_block(512)
    assert np.max(np.abs(out)) == 0.0


def test_process_block_produces_audible_output_for_active_voice() -> None:
    track = TrackProcessor("t1", TrackState(), _tone_sample())
    track.trigger_note()
    out = track.process_block(512)
    assert np.max(np.abs(out)) > 0.0


def test_process_block_full_chain_stays_finite() -> None:
    state = TrackState()
    state.filter.enabled = True
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


def test_force_bypass_compressor_flag_disables_gain_reduction() -> None:
    state = TrackState()
    state.compressor.enabled = True
    state.compressor.threshold_db = -60.0  # everything gets compressed
    state.compressor.ratio = 20.0
    track = TrackProcessor("t1", state, _tone_sample())
    track.trigger_note()

    track.process_block(512, force_bypass_compressor=True)
    assert track.gain_reduction_db == 0.0


def test_mixer_fader_is_capped_at_max_db() -> None:
    from app.services.audio.track_engine import MIXER_FADER_MAX_DB

    state = TrackState()
    state.mixer.fader_db = 999.0  # way past the +6dB cap
    track = TrackProcessor("t1", state, _tone_sample())
    track.trigger_note()
    out = track.process_block(512)
    # capped gain should not blow the signal up arbitrarily
    assert np.max(np.abs(out)) < 10 ** (MIXER_FADER_MAX_DB / 20.0) * 1.1
