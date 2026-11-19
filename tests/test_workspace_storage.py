# ==============================================================================
# Program: tests/test_workspace_storage.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: tests app/storage/workspace.py: the on-disk layout of a workspace folder (code/audio/3d/
# vfx/cache), the project.ideas save/load round trip, and the path-containment helpers that stop
# a workspace operation from escaping its root.
# Run: python3 -m pytest tests/test_workspace_storage.py -q
# ==============================================================================

import os
import pytest
from app.storage.workspace import save_workspace, load_workspace, get_workspace_dir, init_workspace, is_safe_subpath
from app.security import SecurityError

# init_workspace must create the root and every standard subfolder; other modules rely on these
# existing (compiler uses code/ and cache/, audio uses audio/, the engine uses 3d/ and vfx/).
def test_init_workspace(tmp_path):
    ws_path = str(tmp_path / "my_workspace")
    init_workspace(ws_path)
    assert os.path.isdir(ws_path)
    assert os.path.isdir(os.path.join(ws_path, "code"))
    assert os.path.isdir(os.path.join(ws_path, "audio"))
    assert os.path.isdir(os.path.join(ws_path, "3d"))
    assert os.path.isdir(os.path.join(ws_path, "vfx"))
    assert os.path.isdir(os.path.join(ws_path, "cache"))

# Pure string-level checks of the containment rule: a child passes, a sibling fails, and a path
# that climbs out via ".." fails even though its prefix looks like the parent.
def test_safe_subpath():
    assert is_safe_subpath("/a/b/c", "/a/b/c/d")
    assert not is_safe_subpath("/a/b/c", "/a/b/d")
    assert not is_safe_subpath("/a/b/c", "/a/b/c/../d")

# Round trip of the simplest state (editor text and filename) through project.ideas.
def test_save_load_workspace(tmp_path):
    ws_path = str(tmp_path / "my_workspace")
    os.makedirs(ws_path)
    state = {"ide_content": "print('hello')", "ide_filename": "test.py"}
    save_workspace(ws_path, state)
    
    assert os.path.exists(os.path.join(ws_path, "project.ideas"))
    loaded = load_workspace(ws_path)
    assert loaded["ide_content"] == "print('hello')"

# Regression guard: nested state from the video editor, photo editor and asset manager must come
# back identical, so reopening a project restores every studio panel exactly.
def test_save_load_workspace_preserves_studio_state(tmp_path):
    ws_path = str(tmp_path / "my_workspace")
    os.makedirs(ws_path)
    state = {
        "video_editor": {"tracks": [{"index": 0, "name": "Track 1", "clips": []}]},
        "photo_editor": {"width": 2, "height": 2, "layers": []},
        "asset_manager": {"root_path": ws_path, "current_folder": ""},
    }
    save_workspace(ws_path, state)

    loaded = load_workspace(ws_path)
    assert loaded["video_editor"] == state["video_editor"]
    assert loaded["photo_editor"] == state["photo_editor"]
    assert loaded["asset_manager"] == state["asset_manager"]


# get_workspace_dir must refuse names that escape the workspace.
def test_workspace_path_traversal_blocked(tmp_path):
    ws_path = str(tmp_path / "my_workspace")
    os.makedirs(ws_path)
    with pytest.raises(SecurityError):
        # save_workspace itself always joins a fixed filename, so the reachable traversal surface is the
        # subfolder helper; that is what is exercised.
        # We can't really pass a traversal path to save_workspace easily because
        # it uses os.path.join(path, "project.ideas"), but we can test get_workspace_dir
        get_workspace_dir(ws_path, "../outside")
