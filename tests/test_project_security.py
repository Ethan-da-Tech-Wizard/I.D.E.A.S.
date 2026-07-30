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
