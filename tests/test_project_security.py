# ==============================================================================
# Program: tests/test_project_security.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: tests the safety rules of project save/load in app/project.py (.ideas JSON files):
# extension enforcement, filename sanitising, size/track-count limits and tolerance of malformed
# studio sections. A project file is untrusted input, so load/save must clamp or reject, never trust.
# Run: python3 -m pytest tests/test_project_security.py -q
# ==============================================================================

from __future__ import annotations

from pathlib import Path

import pytest

from app.project import load_project_file, save_project_file
from app.security import SecurityError


# Saving "song" must produce "song.ideas". The editor filename "../x.py" is sanitised on save
# (slashes become underscores), so a project cannot smuggle a path into the editor's file name.
def test_project_save_load_round_trip_enforces_extension(tmp_path: Path) -> None:
    output = tmp_path / "song"
    saved = save_project_file(str(output), {"ide_content": "play_sample('sfx/impact.wav')", "ide_filename": "../x.py"})

    assert saved.endswith(".ideas")
    loaded = load_project_file(saved)
    assert loaded["ide_filename"] == ".._x.py"
    assert loaded["ide_content"] == "play_sample('sfx/impact.wav')"


# A file that is not valid JSON raises SecurityError rather than a raw json error.
def test_project_load_rejects_non_json(tmp_path: Path) -> None:
    project = tmp_path / "bad.ideas"
    project.write_text("not json", encoding="utf-8")

    with pytest.raises(SecurityError):
        load_project_file(str(project))


# The DAW section is limited to 16 tracks; 17 is rejected so a project cannot ask for unbounded work.
def test_project_save_rejects_too_many_tracks(tmp_path: Path) -> None:
    tracks = [{} for _ in range(17)]
    with pytest.raises(SecurityError):
        save_project_file(str(tmp_path / "bad.ideas"), {"daw": {"tracks": tracks}})


# Regression guard: engine, video editor, photo editor and asset manager sections are saved and
# restored exactly as given.
def test_project_round_trip_preserves_studio_and_engine_state(tmp_path: Path) -> None:
    state = {
        "engine": {"nodes": []},
        "video_editor": {"tracks": [{"index": 0, "name": "Track 1", "clips": []}]},
        "photo_editor": {"width": 4, "height": 4, "layers": []},
        "asset_manager": {"root_path": "/workspace/assets", "current_folder": ""},
    }
    saved = save_project_file(str(tmp_path / "song"), state)
    loaded = load_project_file(saved)

    assert loaded["engine"] == {"nodes": []}
    assert loaded["video_editor"] == {"tracks": [{"index": 0, "name": "Track 1", "clips": []}]}
    assert loaded["photo_editor"] == {"width": 4, "height": 4, "layers": []}
    assert loaded["asset_manager"] == {"root_path": "/workspace/assets", "current_folder": ""}


# The video timeline has the same 16-track limit as the DAW.
def test_project_save_rejects_too_many_video_editor_tracks(tmp_path: Path) -> None:
    tracks = [{} for _ in range(17)]
    with pytest.raises(SecurityError):
        save_project_file(str(tmp_path / "bad.ideas"), {"video_editor": {"tracks": tracks}})


# Wrong-typed sections (string, list, number, null) are dropped instead of crashing; loaders downstream only see dicts or nothing.
def test_project_ignores_non_dict_studio_state(tmp_path: Path) -> None:
    saved = save_project_file(
        str(tmp_path / "song"),
        {"engine": "not a dict", "video_editor": [], "photo_editor": 5, "asset_manager": None},
    )
    loaded = load_project_file(saved)

    assert "engine" not in loaded
    assert "video_editor" not in loaded
    assert "photo_editor" not in loaded
    assert "asset_manager" not in loaded
