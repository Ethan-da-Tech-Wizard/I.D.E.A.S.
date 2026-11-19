# ==============================================================================
# Program: project.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: Manages project metadata models, manifest serialization, file format versioning, and project load/save operations across IDE workspaces and creative audio/game assets.
# ==============================================================================
# How this module fits: it defines the on-disk shape of a single-file ".ideas" project (a JSON object) and the two public entry points
# save_project_file / load_project_file. The main window imports both (app/application.py) but its Save/Load Workspace menu items currently go through
# app/storage/workspace.py instead, so this format is exercised mainly by tests/test_project_security.py and the project-file code paths.
# All real disk access is delegated to app/security.py (size caps, suffix check, UTF-8 and JSON validation); this module adds the SHAPE rules:
# which keys are allowed, how big each may be, and what default to use when a value is missing or the wrong type.
# Project file layout (all keys optional except version):
#     {"version": 1, "ide_content": str, "ide_filename": str, "daw": {...}, "engine": {...},
#      "video_editor": {...}, "photo_editor": {...}, "asset_manager": {...}}
# Inputs: a raw dict (from the UI or a parsed file). Outputs: a cleaned dict containing only known keys, or SecurityError.
# Design decisions: unknown keys are DROPPED (whitelisting), so a hostile or corrupt file cannot smuggle arbitrary data into the app;
# widget sections are kept as opaque dicts because each owning widget validates its own state in load_state().
# Known limits: the file's "version" is always overwritten with PROJECT_VERSION, so there is no migration path for future versions yet;
# ide_filename has path separators replaced but a bare ".." name is still accepted; the contents of the "daw"/"engine"/etc. dicts are not inspected
# beyond the track counts below, so only the overall byte cap protects against huge nested values.
# Note: about 880 lines of repeated placeholder comments that sat after load_project_file were removed in this pass; they carried no information.

from __future__ import annotations

import json
from typing import Any

from app.security import MAX_PROJECT_FILE_BYTES, SecurityError, read_json_file_safely, write_text_file_safely


# Schema version written into every saved file; bump it when the layout changes incompatibly and add migration code in sanitize_project_state.
PROJECT_VERSION = 1
# Upper bounds. 500,000 characters is roughly 10,000 lines of code, far beyond a lesson-sized script; 160 characters keeps the file name within common
# filesystem limits; 16 tracks is the DAW/video timeline cap, which keeps the UI responsive and the JSON small.
MAX_IDE_CONTENT_CHARS = 500_000
MAX_FILENAME_CHARS = 160
MAX_TRACKS = 16


def sanitize_project_state(raw_state: dict[str, Any]) -> dict[str, Any]:
    """Validate a raw project dictionary and return a cleaned copy.

    Args:
        raw_state: untrusted dict (UI state or parsed JSON).

    Returns:
        A new dict with "version" plus only the recognised keys that had the right types.

    Raises:
        SecurityError: IDE text longer than MAX_IDE_CONTENT_CHARS, or more than MAX_TRACKS DAW or video tracks.
    """
    # Always stamp the current version, ignoring whatever the input claimed.
    state: dict[str, Any] = {"version": PROJECT_VERSION}

    # A missing or non-string ide_content is skipped quietly (the project simply has no code tab); an oversized one is an error, not truncated,
    # so the user is never silently handed a cut-off file.
    ide_content = raw_state.get("ide_content")
    if isinstance(ide_content, str):
        if len(ide_content) > MAX_IDE_CONTENT_CHARS:
            raise SecurityError("IDE content is too large for a project file.")
        state["ide_content"] = ide_content

    # Fall back to "main.py" for anything unusable; replacing both slash styles stops a name like "../x.py" from escaping the workspace folder when
    # it is later joined onto a path.
    ide_filename = raw_state.get("ide_filename", "main.py")
    if not isinstance(ide_filename, str) or not ide_filename or len(ide_filename) > MAX_FILENAME_CHARS:
        ide_filename = "main.py"
    state["ide_filename"] = ide_filename.replace("/", "_").replace("\\", "_")

    # The DAW section is kept as-is after a track-count check; its own load_state() validates the individual tracks.
    daw = raw_state.get("daw")
    if isinstance(daw, dict):
        tracks = daw.get("tracks", [])
        if isinstance(tracks, list) and len(tracks) > MAX_TRACKS:
            raise SecurityError("Project contains too many DAW tracks.")
        state["daw"] = daw

    # Each of these is opaque, already-bounded state produced by the owning widget's own get_state() (Engine scene graph limits, DAW-style track
    # caps for the video timeline, etc.); the project file's overall MAX_PROJECT_FILE_BYTES cap (enforced in write_text_file_safely) is the
    # backstop against runaway growth, same as it already is for "daw" above.
    # Each of these is opaque, already-bounded state produced by the owning
    # widget's own get_state() (Engine scene graph limits, DAW-style track
    # caps for the video timeline, etc.); the project file's overall
    # MAX_PROJECT_FILE_BYTES cap (enforced in write_text_file_safely) is the
    # backstop against runaway growth, same as it already is for "daw" above.
    engine = raw_state.get("engine")
    if isinstance(engine, dict):
        state["engine"] = engine

    # Video timeline gets the same track cap as the DAW because both are lists of lanes rendered one widget each.
    video_editor = raw_state.get("video_editor")
    if isinstance(video_editor, dict):
        tracks = video_editor.get("tracks", [])
        if isinstance(tracks, list) and len(tracks) > MAX_TRACKS:
            raise SecurityError("Project contains too many video timeline tracks.")
        state["video_editor"] = video_editor

    # Photo editor and asset manager have no count limit here; non-dict values are simply ignored.
    photo_editor = raw_state.get("photo_editor")
    if isinstance(photo_editor, dict):
        state["photo_editor"] = photo_editor

    asset_manager = raw_state.get("asset_manager")
    if isinstance(asset_manager, dict):
        state["asset_manager"] = asset_manager

    # Anything not copied above (unknown keys) is deliberately lost.
    return state


def save_project_file(path: str, state: dict[str, Any]) -> str:
    """Sanitise and write a project to disk.

    Args:
        path: destination chosen by the user; ".ideas" is appended/enforced by write_text_file_safely.
        state: project dict as produced by the UI.

    Returns:
        The final path that was written.

    Raises:
        SecurityError: invalid state, content over MAX_PROJECT_FILE_BYTES, or an unsafe destination.
    """
    # Clean first, so what reaches the disk can always be loaded back by load_project_file.
    clean_state = sanitize_project_state(state)
    # sort_keys makes the file deterministic, which keeps version-control diffs small; indent=2 keeps it human readable.
    content = json.dumps(clean_state, indent=2, sort_keys=True)
    # The trailing newline follows the POSIX text-file convention; the ".ideas" argument is the required suffix and the last argument is the byte cap.
    return write_text_file_safely(path, content + "\n", ".ideas", MAX_PROJECT_FILE_BYTES)


def load_project_file(path: str) -> dict[str, Any]:
    """Read, validate and sanitise a project file.

    Args:
        path: file path chosen by the user.

    Returns:
        The cleaned project dict (see sanitize_project_state).

    Raises:
        SecurityError: unsafe file, bad JSON, root not an object, or any sanitising rule fails.
    """
    # Size, regular-file, UTF-8 and JSON checks all happen inside read_json_file_safely before we see the data.
    data = read_json_file_safely(path, max_bytes=MAX_PROJECT_FILE_BYTES)
    # Valid JSON can still be a list or a number; the project root must be an object or the .get() calls above would fail.
    if not isinstance(data, dict):
        raise SecurityError("Project root must be an object.")
    return sanitize_project_state(data)
