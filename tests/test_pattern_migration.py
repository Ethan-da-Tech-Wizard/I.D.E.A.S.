from __future__ import annotations

import os

import pytest

from app.services.audio.track_engine import (
    LEGACY_DELAY_FEEDBACK,
    LEGACY_DELAY_MIX,
    LEGACY_DELAY_MS,
    LEGACY_DISTORTION_DRIVE,
    LEGACY_REVERB_DECAY,
    LEGACY_REVERB_MIX,
    TrackState,
    migrate_legacy_track_record,
)
from app.storage.database import (
    PATTERN_SCHEMA_VERSION,
    StorageError,
    get_db_path,
    init_db,
    load_pattern,
    load_pattern_v2,
    save_pattern,
)


@pytest.fixture(autouse=True)
def isolated_app_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("ID_APP_DATA_DIR", str(tmp_path))
    yield


# -- TrackState.from_legacy_dict / migrate_legacy_track_record -----------

def test_from_legacy_dict_defaults_are_all_bypassed_when_fx_off() -> None:
    state = TrackState.from_legacy_dict({"fx_state": [False, False, False]})
    assert state.distortion_drive == 0.0
    assert state.delay_enabled is False
    assert state.reverb_enabled is False
    assert state.filter.enabled is False
    assert state.lfo.enabled is False
    assert state.mod_envelope.enabled is False
    assert state.compressor.enabled is False
    assert state.gate_length_pct == 100.0
    assert state.loop.enabled is False
    assert state.choke_group is None


def test_from_legacy_dict_maps_old_fx_booleans_to_legacy_fixed_params() -> None:
    """The whole point of migration is that the old sound doesn't change --
    the new continuous knobs must land on the exact old fixed values."""
    state = TrackState.from_legacy_dict({"fx_state": [True, True, True]})
    assert state.distortion_drive == LEGACY_DISTORTION_DRIVE
    assert state.delay_enabled is True
    assert state.delay_ms == LEGACY_DELAY_MS
    assert state.delay_feedback == LEGACY_DELAY_FEEDBACK
    assert state.delay_mix == LEGACY_DELAY_MIX
    assert state.reverb_enabled is True
    assert state.reverb_decay == LEGACY_REVERB_DECAY
    assert state.reverb_mix == LEGACY_REVERB_MIX


def test_from_legacy_dict_handles_missing_fx_state() -> None:
    state = TrackState.from_legacy_dict({})
    assert state.distortion_drive == 0.0
    assert state.delay_enabled is False
    assert state.reverb_enabled is False


def test_from_legacy_dict_partial_fx_combo() -> None:
    state = TrackState.from_legacy_dict({"fx_state": [False, True, False]})
    assert state.distortion_drive == 0.0
    assert state.delay_enabled is True
    assert state.reverb_enabled is False


def test_migrate_legacy_track_record_preserves_sample_selection_fields() -> None:
    old = {
        "relative_path": "electronic/tr8-kick1.wav",
        "fx_state": [True, False, True],
        "pitch_semitones": -2,
        "steps": [True, False, True, False],
    }
    record = migrate_legacy_track_record(old)
    assert record["relative_path"] == "electronic/tr8-kick1.wav"
    assert record["pitch_semitones"] == -2
    assert record["steps"] == [True, False, True, False]
    assert record["chance"] == 100  # default, old shape had no chance field
    assert record["distortion_drive"] == LEGACY_DISTORTION_DRIVE
    assert record["reverb_enabled"] is True
    assert record["delay_enabled"] is False


def test_migrate_legacy_track_record_round_trips_through_track_state_from_dict() -> None:
    """The migrated record must be a valid v2 record -- TrackState.from_dict
    should be able to consume it without error, since that's what the
    future Phase 3 UI will do with load_pattern_v2()'s output."""
    old = {"relative_path": "x.wav", "fx_state": [True, True, False], "pitch_semitones": 3, "steps": [True]}
    record = migrate_legacy_track_record(old)
    restored_state = TrackState.from_dict(record)
    assert restored_state.distortion_drive == LEGACY_DISTORTION_DRIVE
    assert restored_state.delay_enabled is True
    assert restored_state.reverb_enabled is False


# -- database schema_version / ALTER TABLE migration ----------------------

def test_init_db_creates_schema_version_column() -> None:
    init_db()
    import sqlite3
    conn = sqlite3.connect(get_db_path())
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(patterns)")}
    finally:
        conn.close()
    assert "schema_version" in columns


def test_init_db_alters_existing_table_missing_schema_version_column(tmp_path, monkeypatch) -> None:
    """Simulates upgrading from a pre-migration install: a patterns table
    that predates the schema_version column must get it added safely,
    without losing existing rows."""
    import sqlite3

    data_dir = tmp_path / "legacy_install"
    data_dir.mkdir()
    monkeypatch.setenv("ID_APP_DATA_DIR", str(data_dir))
    db_path = os.path.join(str(data_dir), "id_app.db")

    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE patterns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            bpm INTEGER NOT NULL,
            tracks_json TEXT NOT NULL,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute(
        "INSERT INTO patterns (name, bpm, tracks_json) VALUES (?, ?, ?)",
        ("Pre-existing Pattern", 120, "[]"),
    )
    conn.commit()
    conn.close()

    init_db()

    conn = sqlite3.connect(db_path)
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(patterns)")}
        row = conn.execute(
            "SELECT schema_version FROM patterns WHERE name = ?", ("Pre-existing Pattern",)
        ).fetchone()
    finally:
        conn.close()
    assert "schema_version" in columns
    assert row is not None
    assert row[0] == 1  # pre-existing rows are implicitly schema_version 1


# -- load_pattern vs. load_pattern_v2 --------------------------------------

def test_save_pattern_defaults_to_schema_version_1() -> None:
    init_db()
    save_pattern("Legacy Save", 120, [{"relative_path": "a.wav", "fx_state": [True, False, False],
                                        "pitch_semitones": 0, "steps": [True, False]}])
    plain = load_pattern("Legacy Save")
    assert plain["tracks"][0]["fx_state"] == [True, False, False]  # untouched, old shape


def test_load_pattern_v1_shape_is_never_migrated() -> None:
    """load_pattern() must keep returning the old shape forever -- the
    current daw.py UI depends on it and hasn't been rewired to v2 yet."""
    init_db()
    save_pattern("Stays Old", 120, [{"relative_path": "a.wav", "fx_state": [False, True, False],
                                      "pitch_semitones": 1, "steps": [True]}])
    result = load_pattern("Stays Old")
    assert "fx_state" in result["tracks"][0]
    assert "delay_enabled" not in result["tracks"][0]


def test_load_pattern_v2_migrates_legacy_rows() -> None:
    init_db()
    save_pattern("Needs Migration", 120, [{"relative_path": "a.wav", "fx_state": [True, True, True],
                                            "pitch_semitones": -3, "steps": [True, False, True]}])
    migrated = load_pattern_v2("Needs Migration")
    assert migrated["schema_version"] == PATTERN_SCHEMA_VERSION
    track = migrated["tracks"][0]
    assert track["distortion_drive"] == LEGACY_DISTORTION_DRIVE
    assert track["delay_enabled"] is True
    assert track["reverb_enabled"] is True
    assert track["pitch_semitones"] == -3
    assert track["steps"] == [True, False, True]


def test_load_pattern_v2_does_not_mutate_stored_row() -> None:
    """Migration on read must not silently rewrite storage -- load_pattern()
    called again afterward should still see the original v1 shape."""
    init_db()
    save_pattern("No Silent Write", 120, [{"relative_path": "a.wav", "fx_state": [True, False, False],
                                            "pitch_semitones": 0, "steps": [True]}])
    load_pattern_v2("No Silent Write")
    still_old = load_pattern("No Silent Write")
    assert still_old["tracks"][0]["fx_state"] == [True, False, False]


def test_load_pattern_v2_already_v2_row_passes_through_unmigrated() -> None:
    init_db()
    v2_track = {"relative_path": "a.wav", "steps": [True], "pitch_semitones": 0, "chance": 100,
                "distortion_drive": 3.3, "delay_enabled": False, "delay_ms": 100.0,
                "delay_feedback": 0.1, "delay_mix": 0.1, "reverb_enabled": False,
                "reverb_decay": 0.2, "reverb_mix": 0.2, "gate_length_pct": 80.0}
    save_pattern("Already V2", 120, [v2_track], schema_version=PATTERN_SCHEMA_VERSION)
    result = load_pattern_v2("Already V2")
    assert result["tracks"][0]["distortion_drive"] == 3.3  # unchanged, not re-migrated
    assert result["tracks"][0]["gate_length_pct"] == 80.0


def test_load_pattern_v2_missing_pattern_returns_none() -> None:
    init_db()
    assert load_pattern_v2("does not exist") is None


def test_demo_seed_patterns_migrate_cleanly() -> None:
    """The bundled demo patterns in database.py's init_db() are still
    schema_version 1 literal data -- confirm they survive migration."""
    init_db()
    from app.storage.database import list_patterns

    names = list_patterns()
    assert names  # demos were seeded
    for name in names:
        migrated = load_pattern_v2(name)
        assert migrated is not None
        assert migrated["schema_version"] == PATTERN_SCHEMA_VERSION
        for track in migrated["tracks"]:
            assert "distortion_drive" in track
            assert "relative_path" in track
