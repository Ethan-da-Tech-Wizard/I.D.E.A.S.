from __future__ import annotations

import numpy as np

from app.services.audio.dsp import SAMPLE_RATE
from app.services.audio.engine import GLOBAL_VOICE_CAP, AudioEngine, ChokeGroupRegistry
from app.services.audio.track_engine import TrackProcessor, TrackState


def _tone_sample(seconds: float = 2.0, freq: float = 220.0) -> np.ndarray:
    n = int(SAMPLE_RATE * seconds)
    t = np.arange(n) / SAMPLE_RATE
    tone = (0.5 * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    return np.stack([tone, tone], axis=1)


def _run_callback(engine: AudioEngine, n_blocks: int, block_size: int) -> None:
    out = np.zeros((block_size, 2), dtype=np.float32)
    for _ in range(n_blocks):
        engine._callback(out, block_size, None, None)


def _make_track(engine: AudioEngine, track_id: str, choke_group: int | None = None) -> TrackProcessor:
    state = TrackState()
    state.choke_group = choke_group
    track = TrackProcessor(track_id, state, _tone_sample())
    engine.add_track(track)
    return track


# -- step clock --------------------------------------------------------

def test_step_boundary_fires_at_expected_sample_positions() -> None:
    engine = AudioEngine(block_size=64)
    engine.bpm = 120.0
    fired = []
    engine.on_step_boundary = fired.append
    _run_callback(engine, n_blocks=400, block_size=64)  # 400*64 = 25600 samples
    step_frames = engine._current_step_frames()
    # step 0 fires immediately at transport start (matches the legacy
    # AudioEngineThread's behavior of emitting step 0 right away), plus one
    # boundary per subsequent step_frames crossed.
    expected = (400 * 64) // step_frames + 1
    assert len(fired) == expected
    assert fired == sorted(fired)
    assert fired[0] == 0


def test_bpm_change_is_quantized_not_applied_mid_step() -> None:
    engine = AudioEngine(block_size=64)
    engine.bpm = 120.0
    step_frames_before = engine._current_step_frames()
    out = np.zeros((64, 2), dtype=np.float32)

    # advance a few blocks first so we're mid-step (not sitting exactly on
    # the step-0 boundary, which fires immediately at transport start)
    for _ in range(5):
        engine._callback(out, 64, None, None)

    engine.set_bpm(240.0)
    engine._callback(out, 64, None, None)  # still well inside the current step
    assert engine.bpm == 120.0

    # run until we cross the next step boundary
    for _ in range(step_frames_before // 64 + 5):
        engine._callback(out, 64, None, None)
    assert engine.bpm == 240.0


# -- note triggering / gate scheduling ----------------------------------

def test_trigger_step_note_schedules_a_note_off_at_gate_length() -> None:
    engine = AudioEngine(block_size=256)
    track = _make_track(engine, "t1")
    engine.bpm = 120.0
    voice = engine.trigger_step_note("t1", gate_length_pct=50.0)
    assert voice is not None
    assert voice.amp_envelope.is_active
    step_frames = engine._current_step_frames()
    gate_frames = int(step_frames * 0.5)
    # run past the scheduled note-off point
    n_blocks = gate_frames // 256 + 10
    _run_callback(engine, n_blocks, 256)
    assert voice.is_finished


def test_note_on_without_gate_does_not_auto_release() -> None:
    engine = AudioEngine(block_size=256)
    _make_track(engine, "t1")
    voice = engine.note_on("t1")
    _run_callback(engine, 50, 256)
    assert not voice.is_finished  # nothing releases it until note_off_voice()
    engine.note_off_voice(voice)
    _run_callback(engine, 50, 256)
    assert voice.is_finished


def test_trigger_on_unknown_track_returns_none() -> None:
    engine = AudioEngine(block_size=256)
    assert engine.trigger_step_note("nonexistent", gate_length_pct=100.0) is None
    assert engine.note_on("nonexistent") is None


def test_global_voice_cap_refuses_new_triggers_once_full() -> None:
    engine = AudioEngine(block_size=256)
    track = _make_track(engine, "t1")
    for _ in range(GLOBAL_VOICE_CAP):
        track.trigger_note()  # bypass TrackProcessor's own 8-voice cap for this synthetic test
    assert engine._total_voice_count() >= GLOBAL_VOICE_CAP
    assert engine.note_on("t1") is None


# -- choke groups --------------------------------------------------------

def test_choke_group_registry_chokes_other_tracks_only() -> None:
    registry = ChokeGroupRegistry()
    t1 = TrackProcessor("hat_closed", TrackState(choke_group=1), _tone_sample())
    t2 = TrackProcessor("hat_open", TrackState(choke_group=1), _tone_sample())
    t1.choke_group = 1
    t2.choke_group = 1
    registry.register(t1)
    registry.register(t2)

    v2 = t2.trigger_note()
    v1 = t1.trigger_note()
    registry.notify_trigger(t1)

    assert v2.amp_envelope.stage.name in ("RELEASE", "IDLE")  # choked
    assert v1.amp_envelope.stage.name == "ATTACK"  # the triggering track's own voice is untouched


def test_choke_group_does_not_affect_tracks_outside_the_group() -> None:
    engine = AudioEngine(block_size=256)
    t1 = _make_track(engine, "t1", choke_group=1)
    t2 = _make_track(engine, "t2", choke_group=None)
    v2 = engine.note_on("t2")
    engine.note_on("t1")
    assert v2.amp_envelope.stage.name == "ATTACK"


# -- overload shedding ----------------------------------------------------

def test_overload_shedding_engages_and_recovers_under_synthetic_load() -> None:
    engine = AudioEngine(block_size=256)
    for i in range(8):
        _make_track(engine, f"t{i}")
        engine.trigger_step_note(f"t{i}", gate_length_pct=999.0)

    overload_changes = []
    engine.on_overload_changed = overload_changes.append

    # force overload detection without needing real wall-clock slow blocks:
    # feed the internal state machine directly, matching the documented
    # "70% of block period on 2 consecutive blocks" rule.
    block_period = 256 / engine.sample_rate
    engine._update_overload_state(block_period * 0.9, block_period, 256)
    engine._update_overload_state(block_period * 0.9, block_period, 256)
    assert engine._shed_count == 1
    assert overload_changes == [True]

    # long stretch of headroom should let it recover
    for _ in range(int(engine.sample_rate / 256) + 2):
        engine._update_overload_state(block_period * 0.1, block_period, 256)
    assert engine._shed_count == 0
    assert overload_changes == [True, False]


def test_shed_targets_prioritize_lowest_peak_tracks() -> None:
    engine = AudioEngine(block_size=256)
    quiet = _make_track(engine, "quiet")
    loud = _make_track(engine, "loud")
    quiet.peak_level = 0.01
    loud.peak_level = 0.9
    engine._shed_count = 1

    compressor_bypass, reverb_bypass = engine._shed_targets()
    assert compressor_bypass == {"quiet"}
    assert reverb_bypass == set()


# -- master bus / limiter ----------------------------------------------

def test_master_output_never_exceeds_limiter_ceiling() -> None:
    engine = AudioEngine(block_size=512)
    for i in range(8):
        state = TrackState()
        state.mixer.fader_db = 6.0
        state.distortion_drive = 0.0
        track = TrackProcessor(f"t{i}", state, _tone_sample())
        engine.add_track(track)
        track.trigger_note()

    out = np.zeros((512, 2), dtype=np.float32)
    max_abs = 0.0
    for _ in range(20):
        engine._callback(out, 512, None, None)
        max_abs = max(max_abs, float(np.max(np.abs(out))))
    assert max_abs <= engine._limiter.ceiling_linear + 1e-6


def test_remove_track_stops_it_contributing_to_master() -> None:
    engine = AudioEngine(block_size=256)
    track = _make_track(engine, "t1")
    track.trigger_note()
    engine.remove_track("t1")
    out = np.zeros((256, 2), dtype=np.float32)
    engine._callback(out, 256, None, None)
    assert np.max(np.abs(out)) == 0.0


def test_stop_chokes_all_voices_immediately() -> None:
    engine = AudioEngine(block_size=256)
    track = _make_track(engine, "t1")
    voice = track.trigger_note()
    engine.stop()
    assert voice.amp_envelope.stage.name in ("RELEASE", "IDLE")
    assert engine._pending_note_offs == []
