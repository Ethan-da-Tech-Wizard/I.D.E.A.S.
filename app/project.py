from __future__ import annotations

import json
from typing import Any

from app.security import MAX_PROJECT_FILE_BYTES, SecurityError, read_json_file_safely, write_text_file_safely


PROJECT_VERSION = 1
MAX_IDE_CONTENT_CHARS = 500_000
MAX_FILENAME_CHARS = 160
MAX_TRACKS = 16


def sanitize_project_state(raw_state: dict[str, Any]) -> dict[str, Any]:
    state: dict[str, Any] = {"version": PROJECT_VERSION}

    ide_content = raw_state.get("ide_content")
    if isinstance(ide_content, str):
        if len(ide_content) > MAX_IDE_CONTENT_CHARS:
            raise SecurityError("IDE content is too large for a project file.")
        state["ide_content"] = ide_content

    ide_filename = raw_state.get("ide_filename", "main.py")
    if not isinstance(ide_filename, str) or not ide_filename or len(ide_filename) > MAX_FILENAME_CHARS:
        ide_filename = "main.py"
    state["ide_filename"] = ide_filename.replace("/", "_").replace("\\", "_")

    daw = raw_state.get("daw")
    if isinstance(daw, dict):
        tracks = daw.get("tracks", [])
        if isinstance(tracks, list) and len(tracks) > MAX_TRACKS:
            raise SecurityError("Project contains too many DAW tracks.")
        state["daw"] = daw

    return state


def save_project_file(path: str, state: dict[str, Any]) -> str:
    clean_state = sanitize_project_state(state)
    content = json.dumps(clean_state, indent=2, sort_keys=True)
    return write_text_file_safely(path, content + "\n", ".ideas", MAX_PROJECT_FILE_BYTES)


def load_project_file(path: str) -> dict[str, Any]:
    data = read_json_file_safely(path, max_bytes=MAX_PROJECT_FILE_BYTES)
    if not isinstance(data, dict):
        raise SecurityError("Project root must be an object.")
    return sanitize_project_state(data)

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test

# AI Core safe optimization test
