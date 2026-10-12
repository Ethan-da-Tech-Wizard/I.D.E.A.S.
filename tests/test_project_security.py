from __future__ import annotations

from pathlib import Path

import pytest

from app.project import load_project_file, save_project_file
from app.security import SecurityError


def test_project_save_load_round_trip_enforces_extension(tmp_path: Path) -> None:
    output = tmp_path / "song"
    saved = save_project_file(str(output), {"ide_content": "play_sample('sfx/impact.wav')", "ide_filename": "../x.py"})

    assert saved.endswith(".ideas")
    loaded = load_project_file(saved)
    assert loaded["ide_filename"] == ".._x.py"
    assert loaded["ide_content"] == "play_sample('sfx/impact.wav')"


def test_project_load_rejects_non_json(tmp_path: Path) -> None:
    project = tmp_path / "bad.ideas"
    project.write_text("not json", encoding="utf-8")

    with pytest.raises(SecurityError):
        load_project_file(str(project))


def test_project_save_rejects_too_many_tracks(tmp_path: Path) -> None:
    tracks = [{} for _ in range(17)]
    with pytest.raises(SecurityError):
        save_project_file(str(tmp_path / "bad.ideas"), {"daw": {"tracks": tracks}})


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


def test_project_save_rejects_too_many_video_editor_tracks(tmp_path: Path) -> None:
    tracks = [{} for _ in range(17)]
    with pytest.raises(SecurityError):
        save_project_file(str(tmp_path / "bad.ideas"), {"video_editor": {"tracks": tracks}})


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
