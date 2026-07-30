import json
import sqlite3
import os
from PySide6.QtCore import QStandardPaths


class StorageError(RuntimeError):
    """Raised when the app cannot initialize its local private storage."""


MAX_PATTERN_NAME_CHARS = 80
MAX_PATTERN_TRACKS = 16
MAX_PATTERN_JSON_BYTES = 256 * 1024


def get_app_data_dir() -> str:
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
    try:
        os.chmod(app_data_dir, 0o700)
    except OSError:
        pass
    return app_data_dir


def get_db_path() -> str:
    return os.path.join(get_app_data_dir(), "id_app.db")

def init_db() -> None:
    db_path = get_db_path()
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS patterns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                bpm INTEGER NOT NULL,
                tracks_json TEXT NOT NULL,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        conn.close()
    except sqlite3.Error as exc:
        raise StorageError(f"Cannot initialize app database: {db_path}") from exc
    try:
        os.chmod(db_path, 0o600)
    except OSError:
        pass


def save_pattern(name: str, bpm: int, tracks: list[dict]) -> None:
    name = name.strip()
    if not name:
        raise StorageError("Pattern name cannot be empty.")
    if len(name) > MAX_PATTERN_NAME_CHARS:
        raise StorageError("Pattern name is too long.")
    if not 60 <= int(bpm) <= 200:
        raise StorageError("Pattern BPM is outside the safe range.")
    if len(tracks) > MAX_PATTERN_TRACKS:
        raise StorageError("Pattern has too many tracks.")
    tracks_json = json.dumps(tracks)
    if len(tracks_json.encode("utf-8")) > MAX_PATTERN_JSON_BYTES:
        raise StorageError("Pattern is too large.")
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO patterns (name, bpm, tracks_json, updated_at) VALUES (?, ?, ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(name) DO UPDATE SET bpm=excluded.bpm, tracks_json=excluded.tracks_json, "
            "updated_at=CURRENT_TIMESTAMP",
            (name, int(bpm), tracks_json),
        )
        conn.commit()
    except sqlite3.Error as exc:
        raise StorageError(f"Cannot save pattern: {name}") from exc
    finally:
        conn.close()


def load_pattern(name: str) -> dict | None:
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute("SELECT bpm, tracks_json FROM patterns WHERE name = ?", (name,))
        row = cursor.fetchone()
    except sqlite3.Error as exc:
        raise StorageError(f"Cannot load pattern: {name}") from exc
    finally:
        conn.close()
    if row is None:
        return None
    bpm, tracks_json = row
    if len(tracks_json.encode("utf-8")) > MAX_PATTERN_JSON_BYTES:
        raise StorageError("Stored pattern is too large.")
    tracks = json.loads(tracks_json)
    if not isinstance(tracks, list) or len(tracks) > MAX_PATTERN_TRACKS:
        raise StorageError("Stored pattern is invalid.")
    return {"bpm": bpm, "tracks": tracks}


def list_patterns() -> list[str]:
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute("SELECT name FROM patterns ORDER BY name")
        return [row[0] for row in cursor.fetchall()]
    except sqlite3.Error as exc:
        raise StorageError("Cannot list patterns") from exc
    finally:
        conn.close()
