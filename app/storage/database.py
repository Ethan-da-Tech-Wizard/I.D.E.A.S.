# ==============================================================================
# Program: database.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: Encapsulates SQLite database persistence for IDE user preferences, project recents, asset metadata tags, and workspace session caches.
# ==============================================================================
# How this module fits: SQLite storage for the DAW. app/application.py calls init_db() once at start-up (create_app). app/ui/daw.py saves and loads
# step-sequencer "patterns" (save_pattern, load_pattern, list_patterns); app/services/audio_fx.py asks get_app_data_dir() for its render cache folder.
# The database lives in a per-user folder (or the folder named by the ID_APP_DATA_DIR environment variable, which tests use to stay out of the real profile).
# Tables: config(key, value) - generic settings, currently unused by code in this file; patterns(id, name UNIQUE, bpm, tracks_json, updated_at, schema_version).
# tracks_json holds a JSON list of track records. schema_version 1 = original shape {relative_path, fx_state:[distortion, delay, reverb], pitch_semitones, steps};
# version 2 = the full TrackState shape (app/services/audio/track_engine.py). load_pattern returns rows as stored; load_pattern_v2 migrates on read.
# Inputs: pattern name, bpm and a list of track dicts. Outputs: dicts or lists; StorageError (a RuntimeError) for any problem the user could cause or fix.
# Security/robustness design: all SQL uses ? placeholders (no string building with user text); the data folder is chmod 0700 and the database file 0600 so other local
# users cannot read it; every pattern is bounded (80-character name, at most 16 tracks, at most 256 KiB of JSON, tempo 60-200 BPM); the demo patterns are inserted only when the table is empty.
# Known limits noticed (comment pass only):
#   - init_db does not close its connection when an error is raised (the connection leaks until garbage collection).
#   - _fetch_pattern_row can raise json.JSONDecodeError (not StorageError) if a row holds invalid JSON.
#   - There is no schema migration framework beyond the single ALTER TABLE that adds schema_version; no foreign keys or indexes besides the unique name.
#   - A new connection is opened per call; fine for this app's low call rate but not for heavy use.
#   - Imports PySide6 only for QStandardPaths, so this module cannot be imported in an environment without Qt (the other storage modules do not need it).
# Testing: tests/test_pattern_migration.py and tests/test_backend_integration.py set ID_APP_DATA_DIR to a temp folder.

import json
import sqlite3
import os
from PySide6.QtCore import QStandardPaths


class StorageError(RuntimeError):
    """Raised when local private storage (data folder or database) cannot be created, read or written, or when stored/supplied data is invalid."""


# Limits on one saved pattern: name length, number of tracks (16, same as the DAW and video timeline limit) and serialized JSON size (256 KiB) so a corrupt or hostile row cannot exhaust memory.
MAX_PATTERN_NAME_CHARS = 80
MAX_PATTERN_TRACKS = 16
MAX_PATTERN_JSON_BYTES = 256 * 1024

# Schema version 1: the original relative_path/fx_state/pitch_semitones/steps
# shape. Version 2: the DAW_VISION_SPEC.md Data Model contract (gate length,
# trim, loop, envelopes, filter, LFO, mod envelope, compressor, mixer,
# choke group). See app.services.audio.track_engine.migrate_legacy_track_record
# for the actual v1 -> v2 conversion.
# Schema version 1: the original relative_path/fx_state/pitch_semitones/steps
# shape. Version 2: the DAW_VISION_SPEC.md Data Model contract (gate length,
# trim, loop, envelopes, filter, LFO, mod envelope, compressor, mixer,
# choke group). See app.services.audio.track_engine.migrate_legacy_track_record
# for the actual v1 -> v2 conversion.
PATTERN_SCHEMA_VERSION = 2


def get_app_data_dir() -> str:
    """Return (creating it if needed) the private per-user data folder.

    Returns:
        Absolute path. ID_APP_DATA_DIR overrides the platform default (Qt's AppDataLocation); the folder is set to owner-only access (0700) on systems that support it.

    Raises:
        StorageError: no location is available or the folder cannot be created.
    """
    # Environment override first: this is what tests and portable installs use. expanduser lets "~" work.
    app_data_dir = os.environ.get("ID_APP_DATA_DIR")
    if app_data_dir:
        app_data_dir = os.path.abspath(os.path.expanduser(app_data_dir))
    else:
        app_data_dir = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
    if not app_data_dir:
        raise StorageError("No per-user application data directory is available.")
    try:
        if not os.path.exists(app_data_dir):
            os.makedirs(app_data_dir, exist_ok=True)
    except OSError as exc:
        raise StorageError(f"Cannot create app data directory: {app_data_dir}") from exc
    # Tighten permissions on every call (not just creation) in case the folder was created earlier with looser ones. Failure is ignored on filesystems without Unix permissions (e.g. Windows).
    try:
        os.chmod(app_data_dir, 0o700)
    except OSError:
        pass
    return app_data_dir


def get_db_path() -> str:
    """Return the full path of the SQLite file (id_app.db) inside the data folder."""
    return os.path.join(get_app_data_dir(), "id_app.db")

def init_db() -> None:
    """Create the tables, upgrade an older database in place and seed demo patterns on first run.

    Safe to call on every start: CREATE TABLE IF NOT EXISTS never touches existing data, and demos are inserted only when the patterns table is empty.

    Raises:
        StorageError: the database cannot be opened or written.
    """
    db_path = get_db_path()
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        # Generic key/value settings table (reserved for preferences).
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        # One row per saved pattern. name is UNIQUE so saving the same name updates instead of duplicating; bpm and tracks_json are required; the default schema_version 1 matches rows written before the column existed.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS patterns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                bpm INTEGER NOT NULL,
                tracks_json TEXT NOT NULL,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                schema_version INTEGER NOT NULL DEFAULT 1
            )
        """)
        # Safe migration for databases created before schema_version existed:
        # CREATE TABLE IF NOT EXISTS is a no-op on an existing table, so an
        # ALTER TABLE is required to actually add the column for anyone
        # upgrading from an older install. Existing rows implicitly become
        # schema_version 1, which is correct -- they ARE version 1 data.
        # Safe migration for databases created before schema_version existed:
        # CREATE TABLE IF NOT EXISTS is a no-op on an existing table, so an
        # ALTER TABLE is required to actually add the column for anyone
        # upgrading from an older install. Existing rows implicitly become
        # schema_version 1, which is correct -- they ARE version 1 data.
        cursor.execute("PRAGMA table_info(patterns)")
        existing_columns = {row[1] for row in cursor.fetchall()}
        if "schema_version" not in existing_columns:
            cursor.execute(
                "ALTER TABLE patterns ADD COLUMN schema_version INTEGER NOT NULL DEFAULT 1"
            )
        conn.commit()

        # First run only: seed the library with demo beats so the DAW is not empty. Seeding is skipped as soon as any pattern exists; if the user deletes every pattern, the demos come back on the next start.
        # Check if patterns table is empty, and insert initial demos
        cursor.execute("SELECT COUNT(*) FROM patterns")
        count = cursor.fetchone()[0]
        if count == 0:
            # Helper that builds a step list of 128 booleans (128 sixteenth-note steps = 8 bars of 4/4) with True at the given indices; out-of-range indices are ignored.
            def active_steps(*indices) -> list[bool]:
                steps = [False] * 128
                for idx in indices:
                    if 0 <= idx < 128:
                        steps[idx] = True
                return steps

            # Demo table: pattern name -> (bpm, list of track records in the schema version 1 shape). Each track has: relative_path = a sample inside app/resources/samples,
            # fx_state = [distortion, delay, reverb] booleans, pitch_semitones = pitch shift, steps = the 128 on/off values. The long index lists are hand-written rhythms (kick on the beat,
            # hi-hats on offbeats, and so on) and are the data of the demo, so they are left as they are.
            demos = {
                # Demo pattern at 128 BPM; its track list follows (one dict per sample layer).
                "Rap Beat": (128, [
                    # Layer trap_kick.wav: effects distortion; pitch -2 semitones; 29 of 128 steps active.
                    {
                        "relative_path": "genre/trap808/trap_kick.wav",
                        "fx_state": [True, False, False],
                        "pitch_semitones": -2,
                        "steps": active_steps(0, 8, 14, 16, 24, 28, 32, 40, 46, 48, 56, 58, 60, 62, 64, 72, 78, 80, 88, 92, 96, 104, 110, 112, 120, 122, 124, 125, 126)
                    },
                    # Layer trap_snare_clap.wav: effects reverb; pitch +0 semitones; 16 of 128 steps active.
                    {
                        "relative_path": "genre/trap808/trap_snare_clap.wav",
                        "fx_state": [False, False, True],
                        "pitch_semitones": 0,
                        "steps": active_steps(8, 24, 30, 31, 40, 56, 62, 63, 72, 88, 94, 104, 120, 125, 126, 127)
                    },
                    # Layer tr8-highhat1.wav: effects delay; pitch +2 semitones; 81 of 128 steps active.
                    {
                        "relative_path": "electronic/tr8-highhat1.wav",
                        "fx_state": [False, True, False],
                        "pitch_semitones": 2,
                        "steps": active_steps(0, 2, 4, 6, 8, 10, 12, 14, 15, 16, 18, 20, 22, 24, 26, 27, 28, 29, 30, 32, 34, 36, 38, 39, 40, 41, 42, 44, 46, 48, 50, 52, 54, 56, 58, 60, 61, 62, 63, 64, 66, 68, 70, 72, 74, 76, 78, 80, 82, 84, 85, 86, 87, 88, 90, 92, 94, 96, 98, 100, 102, 104, 106, 108, 110, 112, 113, 114, 115, 116, 117, 118, 119, 120, 121, 122, 123, 124, 125, 126, 127)
                    },
                    # Layer synth_whine_lead.wav: effects delay, reverb; pitch +2 semitones; 43 of 128 steps active.
                    {
                        "relative_path": "genre/gfunk/synth_whine_lead.wav",
                        "fx_state": [False, True, True],
                        "pitch_semitones": 2,
                        "steps": active_steps(0, 4, 8, 12, 14, 16, 20, 24, 28, 30, 32, 34, 36, 40, 42, 44, 48, 52, 56, 58, 60, 64, 68, 72, 76, 78, 80, 84, 88, 92, 94, 96, 98, 100, 104, 106, 108, 112, 116, 120, 122, 124, 126)
                    },
                    # Layer talkbox_sweep.wav: effects delay, reverb; pitch +0 semitones; 8 of 128 steps active.
                    {
                        "relative_path": "genre/gfunk/talkbox_sweep.wav",
                        "fx_state": [False, True, True],
                        "pitch_semitones": 0,
                        "steps": active_steps(30, 31, 62, 63, 94, 95, 126, 127)
                    },
                    # Layer airhorn.wav: effects delay, reverb; pitch +2 semitones; 16 of 128 steps active.
                    {
                        "relative_path": "sfx/airhorn.wav",
                        "fx_state": [False, True, True],
                        "pitch_semitones": 2,
                        "steps": active_steps(0, 24, 32, 56, 58, 60, 62, 64, 76, 78, 80, 96, 116, 118, 120, 122)
                    }
                ]),
                # Demo pattern at 128 BPM; its track list follows (one dict per sample layer).
                "House Beat": (128, [
                    # Layer tr8-kick1.wav: effects none (dry); pitch +0 semitones; 41 of 128 steps active.
                    {
                        "relative_path": "electronic/tr8-kick1.wav",
                        "fx_state": [False, False, False],
                        "pitch_semitones": 0,
                        "steps": active_steps(0, 4, 8, 12, 14, 16, 20, 24, 28, 30, 32, 36, 40, 44, 46, 48, 52, 56, 60, 62, 64, 68, 72, 76, 80, 84, 88, 92, 94, 96, 100, 104, 108, 110, 112, 116, 120, 124, 125, 126, 127)
                    },
                    # Layer arena_clap_stack.wav: effects reverb; pitch +1 semitones; 34 of 128 steps active.
                    {
                        "relative_path": "genre/arenasynthpop/arena_clap_stack.wav",
                        "fx_state": [False, False, True],
                        "pitch_semitones": 1,
                        "steps": active_steps(3, 4, 11, 12, 19, 20, 27, 28, 35, 36, 43, 44, 51, 52, 59, 60, 61, 67, 68, 75, 76, 83, 84, 91, 92, 99, 100, 107, 108, 115, 116, 123, 124, 125)
                    },
                    # Layer tr8-openhat1.wav: effects delay, reverb; pitch +0 semitones; 70 of 128 steps active.
                    {
                        "relative_path": "electronic/tr8-openhat1.wav",
                        "fx_state": [False, True, True],
                        "pitch_semitones": 0,
                        "steps": active_steps(1, 2, 3, 5, 6, 7, 9, 10, 11, 13, 14, 15, 18, 21, 22, 23, 26, 29, 30, 31, 34, 37, 38, 39, 42, 45, 46, 47, 50, 53, 54, 55, 58, 61, 62, 63, 66, 69, 70, 71, 74, 77, 78, 79, 82, 85, 86, 87, 90, 93, 94, 95, 98, 101, 102, 103, 106, 109, 110, 111, 114, 117, 118, 119, 121, 122, 123, 125, 126, 127)
                    },
                    # Layer uplifting_chord_stab.wav: effects delay, reverb; pitch +0 semitones; 41 of 128 steps active.
                    {
                        "relative_path": "genre/arenasynthpop/uplifting_chord_stab.wav",
                        "fx_state": [False, True, True],
                        "pitch_semitones": 0,
                        "steps": active_steps(2, 6, 10, 13, 14, 18, 22, 26, 29, 30, 34, 38, 42, 45, 46, 50, 54, 58, 61, 62, 66, 70, 74, 77, 78, 82, 86, 90, 93, 94, 98, 102, 106, 109, 110, 114, 118, 122, 124, 125, 126)
                    },
                    # Layer pluck_bright.wav: effects delay; pitch -5 semitones; 34 of 128 steps active.
                    {
                        "relative_path": "synth/plucks/pluck_bright.wav",
                        "fx_state": [False, True, False],
                        "pitch_semitones": -5,
                        "steps": active_steps(3, 7, 11, 15, 19, 23, 27, 31, 34, 35, 39, 43, 47, 51, 55, 59, 63, 67, 71, 75, 79, 83, 87, 91, 95, 98, 99, 103, 107, 111, 115, 119, 123, 127)
                    },
                    # Layer laser_charge.wav: effects delay, reverb; pitch +3 semitones; 22 of 128 steps active.
                    {
                        "relative_path": "sfx/laser_charge.wav",
                        "fx_state": [False, True, True],
                        "pitch_semitones": 3,
                        "steps": active_steps(12, 14, 28, 30, 44, 46, 60, 62, 63, 64, 76, 78, 92, 94, 108, 110, 120, 122, 124, 125, 126, 127)
                    }
                ]),
                # Demo pattern at 128 BPM; its track list follows (one dict per sample layer).
                "Dubstep Beat": (128, [
                    # Layer tr8-kick2.wav: effects distortion; pitch -1 semitones; 21 of 128 steps active.
                    {
                        "relative_path": "electronic/tr8-kick2.wav",
                        "fx_state": [True, False, False],
                        "pitch_semitones": -1,
                        "steps": active_steps(0, 10, 16, 28, 32, 42, 44, 48, 54, 60, 64, 74, 80, 92, 96, 106, 108, 112, 118, 124, 126)
                    },
                    # Layer tr8-snare2.wav: effects delay, reverb; pitch +0 semitones; 24 of 128 steps active.
                    {
                        "relative_path": "electronic/tr8-snare2.wav",
                        "fx_state": [False, True, True],
                        "pitch_semitones": 0,
                        "steps": active_steps(8, 24, 25, 40, 56, 60, 62, 63, 72, 88, 89, 104, 112, 114, 116, 118, 120, 121, 122, 123, 124, 125, 126, 127)
                    },
                    # Layer tr8-highhat1.wav: effects delay; pitch +3 semitones; 56 of 128 steps active.
                    {
                        "relative_path": "electronic/tr8-highhat1.wav",
                        "fx_state": [False, True, False],
                        "pitch_semitones": 3,
                        "steps": active_steps(2, 3, 4, 6, 10, 12, 14, 18, 19, 20, 22, 26, 28, 30, 34, 35, 36, 38, 42, 44, 46, 50, 51, 52, 54, 58, 60, 62, 66, 67, 68, 70, 74, 76, 78, 82, 83, 84, 86, 90, 92, 94, 98, 99, 100, 102, 106, 108, 110, 114, 115, 116, 118, 122, 124, 126)
                    },
                    # Layer wub.wav: effects distortion, delay, reverb; pitch -3 semitones; 64 of 128 steps active.
                    {
                        "relative_path": "electronic/wub.wav",
                        "fx_state": [True, True, True],
                        "pitch_semitones": -3,
                        "steps": active_steps(2, 3, 5, 6, 10, 11, 12, 13, 18, 19, 21, 22, 26, 27, 29, 30, 34, 35, 37, 38, 42, 43, 44, 45, 50, 51, 53, 54, 58, 59, 61, 62, 66, 67, 69, 70, 74, 75, 76, 77, 82, 83, 85, 86, 90, 91, 93, 94, 98, 99, 101, 102, 106, 107, 108, 109, 114, 115, 117, 118, 122, 123, 125, 126)
                    },
                    # Layer sub37-3-monster.wav: effects distortion, reverb; pitch +0 semitones; 20 of 128 steps active.
                    {
                        "relative_path": "electronic/sub37-3-monster.wav",
                        "fx_state": [True, False, True],
                        "pitch_semitones": 0,
                        "steps": active_steps(14, 15, 30, 31, 46, 47, 60, 61, 62, 63, 78, 79, 94, 95, 110, 111, 124, 125, 126, 127)
                    },
                    # Layer glitch_stutter.wav: effects distortion, delay; pitch -2 semitones; 26 of 128 steps active.
                    {
                        "relative_path": "sfx/glitch_stutter.wav",
                        "fx_state": [True, True, False],
                        "pitch_semitones": -2,
                        "steps": active_steps(6, 7, 13, 14, 29, 31, 38, 39, 45, 47, 60, 61, 62, 63, 70, 71, 86, 87, 93, 95, 109, 111, 124, 125, 126, 127)
                    }
                ]),
                # Demo pattern at 128 BPM; its track list follows (one dict per sample layer).
                "Rap Dubstep Beat": (128, [
                    # Layer trap_kick.wav: effects distortion; pitch -2 semitones; 28 of 128 steps active.
                    {
                        "relative_path": "genre/trap808/trap_kick.wav",
                        "fx_state": [True, False, False],
                        "pitch_semitones": -2,
                        "steps": active_steps(0, 11, 14, 16, 24, 27, 32, 43, 46, 48, 56, 59, 61, 63, 64, 75, 78, 80, 88, 91, 96, 107, 110, 112, 120, 123, 125, 127)
                    },
                    # Layer trap_snare_clap.wav: effects reverb; pitch +0 semitones; 16 of 128 steps active.
                    {
                        "relative_path": "genre/trap808/trap_snare_clap.wav",
                        "fx_state": [False, False, True],
                        "pitch_semitones": 0,
                        "steps": active_steps(8, 24, 28, 30, 40, 56, 62, 63, 72, 88, 92, 104, 120, 124, 126, 127)
                    },
                    # Layer tr8-highhat1.wav: effects delay, reverb; pitch +1 semitones; 91 of 128 steps active.
                    {
                        "relative_path": "electronic/tr8-highhat1.wav",
                        "fx_state": [False, True, True],
                        "pitch_semitones": 1,
                        "steps": active_steps(0, 2, 4, 6, 8, 10, 12, 14, 15, 16, 17, 18, 19, 20, 22, 24, 26, 28, 30, 31, 32, 33, 34, 35, 36, 38, 40, 42, 44, 46, 47, 48, 49, 50, 51, 52, 54, 56, 58, 60, 62, 63, 64, 65, 66, 67, 68, 70, 72, 74, 76, 78, 79, 80, 81, 82, 83, 84, 86, 88, 90, 92, 94, 95, 96, 97, 98, 99, 100, 102, 104, 106, 108, 110, 111, 112, 113, 114, 115, 116, 117, 118, 119, 120, 121, 122, 123, 124, 125, 126, 127)
                    },
                    # Layer wub.wav: effects distortion, delay, reverb; pitch -1 semitones; 57 of 128 steps active.
                    {
                        "relative_path": "electronic/wub.wav",
                        "fx_state": [True, True, True],
                        "pitch_semitones": -1,
                        "steps": active_steps(2, 3, 6, 7, 12, 13, 14, 18, 19, 22, 23, 28, 29, 30, 34, 35, 38, 39, 44, 45, 46, 50, 51, 54, 55, 60, 61, 62, 66, 67, 70, 71, 76, 77, 78, 82, 83, 86, 87, 92, 93, 94, 98, 99, 102, 103, 108, 109, 110, 114, 115, 118, 119, 124, 125, 126, 127)
                    },
                    # Layer glitch_stutter.wav: effects delay, reverb; pitch +2 semitones; 16 of 128 steps active.
                    {
                        "relative_path": "sfx/glitch_stutter.wav",
                        "fx_state": [False, True, True],
                        "pitch_semitones": 2,
                        "steps": active_steps(14, 15, 30, 31, 46, 47, 62, 63, 78, 79, 94, 95, 110, 111, 126, 127)
                    },
                    # Layer coin.wav: effects delay, reverb; pitch +5 semitones; 23 of 128 steps active.
                    {
                        "relative_path": "sfx/coin.wav",
                        "fx_state": [False, True, True],
                        "pitch_semitones": 5,
                        "steps": active_steps(6, 14, 22, 30, 35, 37, 39, 54, 56, 58, 60, 70, 71, 72, 86, 88, 102, 104, 118, 120, 121, 122, 123)
                    }
                ]),
                # Demo pattern at 128 BPM; its track list follows (one dict per sample layer).
                "Metal Beat": (128, [
                    # Layer tr8-kick5.wav: effects distortion; pitch +0 semitones; 72 of 128 steps active.
                    {
                        "relative_path": "electronic/tr8-kick5.wav",
                        "fx_state": [True, False, False],
                        "pitch_semitones": 0,
                        "steps": active_steps(0, 4, 8, 12, 16, 18, 19, 20, 22, 23, 24, 26, 27, 28, 30, 31, 32, 36, 40, 44, 48, 49, 50, 51, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61, 62, 63, 64, 68, 72, 76, 80, 82, 83, 84, 86, 87, 88, 90, 91, 92, 94, 95, 96, 100, 104, 108, 112, 113, 114, 115, 116, 117, 118, 119, 120, 121, 122, 123, 124, 125, 126, 127)
                    },
                    # Layer tr8-snare1.wav: effects reverb; pitch +0 semitones; 29 of 128 steps active.
                    {
                        "relative_path": "electronic/tr8-snare1.wav",
                        "fx_state": [False, False, True],
                        "pitch_semitones": 0,
                        "steps": active_steps(4, 12, 20, 28, 36, 44, 48, 50, 52, 54, 56, 58, 60, 62, 68, 76, 84, 92, 100, 108, 112, 114, 116, 118, 120, 122, 124, 126, 127)
                    },
                    # Layer tr8-highhat1.wav: effects reverb; pitch +2 semitones; 64 of 128 steps active.
                    {
                        "relative_path": "electronic/tr8-highhat1.wav",
                        "fx_state": [False, False, True],
                        "pitch_semitones": 2,
                        "steps": active_steps(0, 2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 22, 24, 26, 28, 30, 32, 34, 36, 38, 40, 42, 44, 46, 48, 50, 52, 54, 56, 58, 60, 62, 64, 66, 68, 70, 72, 74, 76, 78, 80, 82, 84, 86, 88, 90, 92, 94, 96, 98, 100, 102, 104, 106, 108, 110, 112, 114, 116, 118, 120, 122, 124, 126)
                    },
                    # Layer palm_mute_chug_a.wav: effects distortion, reverb; pitch +0 semitones; 64 of 128 steps active.
                    {
                        "relative_path": "genre/metal/palm_mute_chug_a.wav",
                        "fx_state": [True, False, True],
                        "pitch_semitones": 0,
                        "steps": active_steps(0, 1, 2, 6, 8, 10, 12, 14, 16, 17, 18, 22, 24, 26, 28, 30, 32, 33, 34, 38, 40, 42, 44, 46, 48, 49, 50, 54, 56, 58, 60, 62, 64, 65, 66, 70, 72, 74, 76, 78, 80, 81, 82, 86, 88, 90, 92, 94, 96, 97, 98, 102, 104, 106, 108, 110, 112, 113, 114, 118, 120, 122, 124, 126)
                    },
                    # Layer screech_harmonic.wav: effects delay, reverb; pitch +5 semitones; 8 of 128 steps active.
                    {
                        "relative_path": "genre/metal/screech_harmonic.wav",
                        "fx_state": [False, True, True],
                        "pitch_semitones": 5,
                        "steps": active_steps(15, 31, 47, 63, 79, 95, 111, 127)
                    },
                    # Layer alarm_siren.wav: effects distortion, delay, reverb; pitch +0 semitones; 18 of 128 steps active.
                    {
                        "relative_path": "sfx/alarm_siren.wav",
                        "fx_state": [True, True, True],
                        "pitch_semitones": 0,
                        "steps": active_steps(0, 24, 28, 32, 48, 64, 76, 80, 84, 96, 112, 114, 116, 118, 120, 122, 124, 126)
                    }
                ]),
                # Demo pattern at 128 BPM; its track list follows (one dict per sample layer).
                "Country Beat": (128, [
                    # Layer dusty_kick_soft.wav: effects none (dry); pitch +0 semitones; 32 of 128 steps active.
                    {
                        "relative_path": "genre/boombap/dusty_kick_soft.wav",
                        "fx_state": [False, False, False],
                        "pitch_semitones": 0,
                        "steps": active_steps(0, 6, 8, 14, 16, 22, 24, 30, 32, 38, 40, 46, 48, 54, 56, 62, 64, 70, 72, 78, 80, 86, 88, 94, 96, 102, 104, 110, 112, 118, 120, 126)
                    },
                    # Layer dusty_rim.wav: effects reverb; pitch +0 semitones; 96 of 128 steps active.
                    {
                        "relative_path": "genre/boombap/dusty_rim.wav",
                        "fx_state": [False, False, True],
                        "pitch_semitones": 0,
                        "steps": active_steps(0, 1, 3, 4, 5, 7, 8, 9, 11, 12, 13, 15, 16, 17, 19, 20, 21, 23, 24, 25, 27, 28, 29, 31, 32, 33, 35, 36, 37, 39, 40, 41, 43, 44, 45, 47, 48, 49, 51, 52, 53, 55, 56, 57, 59, 60, 61, 63, 64, 65, 67, 68, 69, 71, 72, 73, 75, 76, 77, 79, 80, 81, 83, 84, 85, 87, 88, 89, 91, 92, 93, 95, 96, 97, 99, 100, 101, 103, 104, 105, 107, 108, 109, 111, 112, 113, 115, 116, 117, 119, 120, 121, 123, 124, 125, 127)
                    },
                    # Layer shaker.wav: effects reverb; pitch +1 semitones; 63 of 128 steps active.
                    {
                        "relative_path": "genre/soulrock/shaker.wav",
                        "fx_state": [False, False, True],
                        "pitch_semitones": 1,
                        "steps": active_steps(2, 3, 6, 7, 10, 11, 14, 15, 18, 19, 22, 23, 26, 27, 30, 31, 34, 35, 38, 39, 42, 43, 46, 47, 50, 51, 54, 55, 58, 59, 62, 63, 66, 67, 70, 71, 74, 75, 78, 79, 82, 83, 86, 87, 90, 91, 94, 95, 98, 99, 102, 103, 106, 107, 110, 111, 114, 118, 119, 122, 123, 126, 127)
                    },
                    # Layer banjo_roll.wav: effects reverb; pitch +0 semitones; 64 of 128 steps active.
                    {
                        "relative_path": "genre/country/banjo_roll.wav",
                        "fx_state": [False, False, True],
                        "pitch_semitones": 0,
                        "steps": active_steps(0, 2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 22, 24, 26, 28, 30, 32, 34, 36, 38, 40, 42, 44, 46, 48, 50, 52, 54, 56, 58, 60, 62, 64, 66, 68, 70, 72, 74, 76, 78, 80, 82, 84, 86, 88, 90, 92, 94, 96, 98, 100, 102, 104, 106, 108, 110, 112, 114, 116, 118, 120, 122, 124, 126)
                    },
                    # Layer fiddle_lick.wav: effects delay, reverb; pitch +2 semitones; 13 of 128 steps active.
                    {
                        "relative_path": "genre/country/fiddle_lick.wav",
                        "fx_state": [False, True, True],
                        "pitch_semitones": 2,
                        "steps": active_steps(0, 8, 16, 24, 32, 48, 64, 72, 80, 88, 96, 112, 120)
                    },
                    # Layer robot_blip.wav: effects delay, reverb; pitch +7 semitones; 24 of 128 steps active.
                    {
                        "relative_path": "sfx/robot_blip.wav",
                        "fx_state": [False, True, True],
                        "pitch_semitones": 7,
                        "steps": active_steps(9, 11, 25, 27, 40, 42, 44, 46, 57, 59, 64, 67, 70, 73, 76, 79, 82, 85, 88, 91, 105, 107, 121, 123)
                    }
                ]),
                # Demo pattern at 128 BPM; its track list follows (one dict per sample layer).
                "Rock Beat": (128, [
                    # Layer dusty_kick.wav: effects none (dry); pitch -1 semitones; 29 of 128 steps active.
                    {
                        "relative_path": "genre/boombap/dusty_kick.wav",
                        "fx_state": [False, False, False],
                        "pitch_semitones": -1,
                        "steps": active_steps(0, 8, 10, 16, 24, 26, 28, 32, 40, 42, 48, 56, 58, 60, 64, 72, 74, 80, 88, 90, 92, 96, 104, 106, 112, 116, 118, 120, 122)
                    },
                    # Layer dusty_snare.wav: effects reverb; pitch +0 semitones; 31 of 128 steps active.
                    {
                        "relative_path": "genre/boombap/dusty_snare.wav",
                        "fx_state": [False, False, True],
                        "pitch_semitones": 0,
                        "steps": active_steps(4, 10, 12, 20, 26, 28, 36, 42, 44, 52, 58, 60, 68, 74, 76, 84, 90, 92, 100, 106, 108, 116, 118, 120, 121, 122, 123, 124, 125, 126, 127)
                    },
                    # Layer tr8-highhat1.wav: effects delay, reverb; pitch +0 semitones; 75 of 128 steps active.
                    {
                        "relative_path": "electronic/tr8-highhat1.wav",
                        "fx_state": [False, True, True],
                        "pitch_semitones": 0,
                        "steps": active_steps(0, 2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 22, 24, 26, 28, 30, 31, 32, 34, 36, 38, 40, 42, 44, 46, 48, 50, 52, 54, 56, 58, 60, 62, 63, 64, 66, 68, 70, 72, 74, 76, 78, 80, 82, 84, 86, 88, 90, 92, 94, 95, 96, 98, 100, 102, 104, 106, 108, 110, 112, 113, 114, 115, 116, 117, 118, 119, 120, 121, 122, 123, 124, 125, 126, 127)
                    },
                    # Layer A_min.wav: effects distortion, reverb; pitch +0 semitones; 48 of 128 steps active.
                    {
                        "relative_path": "instruments/guitar_chords/A_min.wav",
                        "fx_state": [True, False, True],
                        "pitch_semitones": 0,
                        "steps": active_steps(0, 3, 6, 8, 11, 14, 16, 19, 22, 24, 27, 30, 32, 35, 38, 40, 43, 46, 48, 51, 54, 56, 59, 62, 64, 67, 70, 72, 75, 78, 80, 83, 86, 88, 91, 94, 96, 99, 102, 104, 107, 110, 112, 115, 118, 120, 123, 126)
                    },
                    # Layer crash.wav: effects reverb; pitch +0 semitones; 8 of 128 steps active.
                    {
                        "relative_path": "instruments/Drums/crash.wav",
                        "fx_state": [False, False, True],
                        "pitch_semitones": 0,
                        "steps": active_steps(0, 16, 32, 48, 64, 80, 96, 112)
                    },
                    # Layer engine_rev.wav: effects distortion, reverb; pitch -2 semitones; 16 of 128 steps active.
                    {
                        "relative_path": "sfx/engine_rev.wav",
                        "fx_state": [True, False, True],
                        "pitch_semitones": -2,
                        "steps": active_steps(0, 30, 31, 48, 52, 56, 60, 64, 90, 92, 94, 112, 120, 122, 124, 126)
                    }
                ])
            }
            # tracks_json is plain JSON text; the demos do not set schema_version so they take the column default of 1.
            for name, (bpm, tracks) in demos.items():
                tracks_json = json.dumps(tracks)
                cursor.execute(
                    "INSERT INTO patterns (name, bpm, tracks_json, updated_at) VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
                    (name, bpm, tracks_json)
                )
            conn.commit()

        # Closed here on success only; an exception skips this line (see known limits).
        conn.close()
    except sqlite3.Error as exc:
        raise StorageError(f"Cannot initialize app database: {db_path}") from exc
    # Same permission hardening for the database file itself: owner read/write only.
    try:
        os.chmod(db_path, 0o600)
    except OSError:
        pass


def save_pattern(name: str, bpm: int, tracks: list[dict], schema_version: int = 1) -> None:
    """Insert or update a pattern.

    Args:
        name: pattern name (trimmed; 1-80 characters). Saving an existing name overwrites that pattern.
        bpm: tempo, 60-200.
        tracks: list of track dicts (at most 16).
        schema_version: 1 or 2 (see the file header); the caller says which shape `tracks` has.

    Raises:
        StorageError: empty or too long name, tempo out of range, too many tracks, JSON over 256 KiB, or a database error.
        TypeError / ValueError: tracks that json cannot serialize, or a non-numeric bpm.
    """
    # Validate everything before opening the database, so a rejected save never touches the file.
    name = name.strip()
    if not name:
        raise StorageError("Pattern name cannot be empty.")
    if len(name) > MAX_PATTERN_NAME_CHARS:
        raise StorageError("Pattern name is too long.")
    # Chained comparison: tempo must be between 60 and 200 inclusive.
    if not 60 <= int(bpm) <= 200:
        raise StorageError("Pattern BPM is outside the safe range.")
    if len(tracks) > MAX_PATTERN_TRACKS:
        raise StorageError("Pattern has too many tracks.")
    # Measure the encoded byte size, not the character count, since that is what is stored.
    tracks_json = json.dumps(tracks)
    if len(tracks_json.encode("utf-8")) > MAX_PATTERN_JSON_BYTES:
        raise StorageError("Pattern is too large.")
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    try:
        # Upsert: ON CONFLICT(name) updates the existing row, so saving twice never raises a uniqueness error; ? placeholders keep user text out of the SQL.
        conn.execute(
            "INSERT INTO patterns (name, bpm, tracks_json, updated_at, schema_version) "
            "VALUES (?, ?, ?, CURRENT_TIMESTAMP, ?) "
            "ON CONFLICT(name) DO UPDATE SET bpm=excluded.bpm, tracks_json=excluded.tracks_json, "
            "updated_at=CURRENT_TIMESTAMP, schema_version=excluded.schema_version",
            (name, int(bpm), tracks_json, int(schema_version)),
        )
        conn.commit()
    except sqlite3.Error as exc:
        raise StorageError(f"Cannot save pattern: {name}") from exc
    # Always close, even after an error; the with-statement form is not used because sqlite3's context manager commits but does not close.
    finally:
        conn.close()


def _fetch_pattern_row(name: str) -> tuple[int, str, int] | None:
    """Fetch one pattern row and validate it.

    Args:
        name: exact pattern name.

    Returns:
        (bpm, tracks list, schema_version), or None if there is no such pattern.

    Raises:
        StorageError: database error, stored JSON over the size limit, or not a list of at most 16 tracks.
    """
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute(
            "SELECT bpm, tracks_json, schema_version FROM patterns WHERE name = ?", (name,)
        )
        row = cursor.fetchone()
    except sqlite3.Error as exc:
        raise StorageError(f"Cannot load pattern: {name}") from exc
    finally:
        conn.close()
    # Row missing is a normal result (None), not an error.
    if row is None:
        return None
    bpm, tracks_json, schema_version = row
    # Re-check limits when reading, because the database file could have been edited outside the app.
    if len(tracks_json.encode("utf-8")) > MAX_PATTERN_JSON_BYTES:
        raise StorageError("Stored pattern is too large.")
    tracks = json.loads(tracks_json)
    if not isinstance(tracks, list) or len(tracks) > MAX_PATTERN_TRACKS:
        raise StorageError("Stored pattern is invalid.")
    return bpm, tracks, schema_version


def load_pattern(name: str) -> dict | None:
    """Load a pattern exactly as stored (no migration).

    Args:
        name: pattern name.

    Returns:
        {"bpm": int, "tracks": [...]} with tracks in whatever schema the row was saved with, or None if missing. The current app/ui/daw.py UI calls this and expects
        version 1 records; it does not understand the version 2 TrackState shape yet (docs/DAW_VISION_SPEC.md's Phase 3). Use load_pattern_v2() for the migrated shape.
    """
    result = _fetch_pattern_row(name)
    if result is None:
        return None
    bpm, tracks, _schema_version = result
    return {"bpm": bpm, "tracks": tracks}


def load_pattern_v2(name: str) -> dict | None:
    """Load a pattern, upgrading old rows to schema version 2 on the fly.

    Args:
        name: pattern name.

    Returns:
        {"bpm", "tracks", "schema_version": 2} or None if missing. Version 1 rows run through track_engine.migrate_legacy_track_record. Nothing is written back:
        migration is a read-time transform, not a silent change of stored data; call save_pattern(..., schema_version=2) to persist an upgrade.
    """
    # Imported here to avoid a circular import and to keep Qt/numpy-heavy audio modules out of plain storage use.
    from app.services.audio.track_engine import migrate_legacy_track_record

    result = _fetch_pattern_row(name)
    if result is None:
        return None
    bpm, tracks, schema_version = result
    # Only rows older than the current version are converted; version 2 rows pass through untouched.
    if schema_version < PATTERN_SCHEMA_VERSION:
        tracks = [migrate_legacy_track_record(t) for t in tracks]
    return {"bpm": bpm, "tracks": tracks, "schema_version": PATTERN_SCHEMA_VERSION}


def list_patterns() -> list[str]:
    """Return all pattern names sorted alphabetically.

    Raises:
        StorageError: database error.
    """
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute("SELECT name FROM patterns ORDER BY name")
        return [row[0] for row in cursor.fetchall()]
    except sqlite3.Error as exc:
        raise StorageError("Cannot list patterns") from exc
    finally:
        conn.close()
