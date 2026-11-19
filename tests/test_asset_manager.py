# ==============================================================================
# Program: tests/test_asset_manager.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: tests the non-GUI model of the asset manager (app/ui/assets/asset_manager.py):
# discovery, folder/search/kind filters, rename, delete, path resolution, suffix-to-kind routing and
# the drag-and-drop MIME payload codec. Every mutating operation must stay inside the workspace root
# and raise SecurityError otherwise; no QApplication is needed because only the model is used.
# Run: python3 -m pytest tests/test_asset_manager.py -q
# ==============================================================================

from __future__ import annotations

from pathlib import Path

import pytest

from app.security import SecurityError
from app.ui.assets.catalog import AssetKind
from app.ui.assets.asset_manager import (
    AssetManagerModel,
    decode_asset_mime_payload,
    encode_asset_mime_payload,
    kind_for_relative_path,
)


# Fixture helper (not a test): builds a small workspace with two audio files, one image and one
# top-level file. Used by almost every test so folder/kind expectations stay in one place.
def make_workspace(tmp_path: Path) -> Path:
    """Create audio/, images/ and a root-level file under tmp_path and return tmp_path."""
    (tmp_path / "audio").mkdir()
    (tmp_path / "audio" / "kick.wav").write_bytes(b"RIFF")
    (tmp_path / "audio" / "snare.wav").write_bytes(b"RIFF")
    (tmp_path / "images").mkdir()
    (tmp_path / "images" / "cover.png").write_bytes(b"png")
    (tmp_path / "top_level.py").write_text("print('hi')", encoding="utf-8")
    return tmp_path


# Scenario: the model scans the workspace on construction. Also shows that supported kinds and
# other files are listed, and that the root ("") and each subfolder appear in folders().
def test_asset_discovery_in_temp_directory(tmp_path: Path) -> None:
    make_workspace(tmp_path)
    model = AssetManagerModel(str(tmp_path))

    names = sorted(entry.name for entry in model.catalog.entries)
    assert names == ["cover.png", "kick.wav", "snare.wav", "top_level.py"]
    assert "" in model.folders()
    assert "audio" in model.folders()
    assert "images" in model.folders()


# Folder filter is exact: "audio" yields only that folder's files and "" means the root level.
def test_filter_by_folder(tmp_path: Path) -> None:
    make_workspace(tmp_path)
    model = AssetManagerModel(str(tmp_path))

    audio_entries = model.filter_entries(folder="audio")
    assert sorted(e.name for e in audio_entries) == ["kick.wav", "snare.wav"]

    root_entries = model.filter_entries(folder="")
    assert [e.name for e in root_entries] == ["top_level.py"]


# Search is a name substring match; a miss returns an empty list (not None or an error).
def test_filter_by_search_text(tmp_path: Path) -> None:
    make_workspace(tmp_path)
    model = AssetManagerModel(str(tmp_path))

    matches = model.filter_entries(search_text="kick")
    assert [e.name for e in matches] == ["kick.wav"]

    assert model.filter_entries(search_text="nonexistent") == []


# Kind filter selects by media type regardless of folder.
def test_filter_by_kind(tmp_path: Path) -> None:
    make_workspace(tmp_path)
    model = AssetManagerModel(str(tmp_path))

    audio_only = model.filter_entries(kind=AssetKind.AUDIO)
    assert sorted(e.name for e in audio_only) == ["kick.wav", "snare.wav"]

    image_only = model.filter_entries(kind=AssetKind.IMAGE)
    assert [e.name for e in image_only] == ["cover.png"]


# All three filters AND together (folder + text + kind), which is how the panel applies them.
def test_combined_filters(tmp_path: Path) -> None:
    make_workspace(tmp_path)
    model = AssetManagerModel(str(tmp_path))

    result = model.filter_entries(folder="audio", search_text="snare", kind=AssetKind.AUDIO)
    assert [e.name for e in result] == ["snare.wav"]


# Rename returns the new workspace-relative path and really moves the file on disk.
def test_rename_asset_happy_path(tmp_path: Path) -> None:
    make_workspace(tmp_path)
    model = AssetManagerModel(str(tmp_path))

    new_path = model.rename_asset("audio/kick.wav", "kick_v2.wav")
    assert new_path == "audio/kick_v2.wav"
    assert (tmp_path / "audio" / "kick_v2.wav").exists()
    assert not (tmp_path / "audio" / "kick.wav").exists()


# The new name must be a bare file name; "../" would let a rename move a file out of its folder or
# out of the workspace.
def test_rename_asset_rejects_path_separators_in_new_name(tmp_path: Path) -> None:
    make_workspace(tmp_path)
    model = AssetManagerModel(str(tmp_path))

    with pytest.raises(SecurityError):
        model.rename_asset("audio/kick.wav", "../escaped.wav")


# Renaming onto an existing file must fail instead of silently overwriting it (data loss).
def test_rename_asset_rejects_collision(tmp_path: Path) -> None:
    make_workspace(tmp_path)
    model = AssetManagerModel(str(tmp_path))

    with pytest.raises(SecurityError):
        model.rename_asset("audio/kick.wav", "snare.wav")


# Delete removes the file from disk.
def test_delete_asset_happy_path(tmp_path: Path) -> None:
    make_workspace(tmp_path)
    model = AssetManagerModel(str(tmp_path))

    model.delete_asset("audio/kick.wav")
    assert not (tmp_path / "audio" / "kick.wav").exists()


# Safety: a traversal path aimed at a real file outside the workspace must be refused and the file
# must survive. The victim file is created next to (outside) the workspace and cleaned in finally.
def test_delete_asset_rejects_path_traversal(tmp_path: Path) -> None:
    make_workspace(tmp_path)
    # tmp_path.parent is outside the workspace root but still pytest-managed scratch space.
    outside = tmp_path.parent / "outside_secret.txt"
    outside.write_text("do not delete me", encoding="utf-8")
    model = AssetManagerModel(str(tmp_path))

    try:
        with pytest.raises(SecurityError):
            model.delete_asset("../outside_secret.txt")
        assert outside.exists()
    finally:
        outside.unlink(missing_ok=True)


# Deleting something that is not there is an error, so the UI can tell the user instead of
# pretending success.
def test_delete_asset_rejects_nonexistent_file(tmp_path: Path) -> None:
    make_workspace(tmp_path)
    model = AssetManagerModel(str(tmp_path))

    with pytest.raises(SecurityError):
        model.delete_asset("audio/does_not_exist.wav")


# Maps a logical folder name ("" = root) to an absolute, resolved disk path for "reveal in folder".
def test_folder_disk_path_resolves_within_root(tmp_path: Path) -> None:
    make_workspace(tmp_path)
    model = AssetManagerModel(str(tmp_path))

    assert model.folder_disk_path("audio") == (tmp_path / "audio").resolve()
    assert model.folder_disk_path("") == Path(model.root_path)


# Suffix routing is case-insensitive and unknown suffixes return None (not an exception), which
# drag-and-drop uses to ignore unsupported files.
def test_kind_for_relative_path_routes_by_suffix() -> None:
    assert kind_for_relative_path("audio/kick.wav") == AssetKind.AUDIO
    assert kind_for_relative_path("images/cover.PNG") == AssetKind.IMAGE
    assert kind_for_relative_path("clips/intro.mp4") == AssetKind.VIDEO
    assert kind_for_relative_path("clips/outro.MOV") == AssetKind.VIDEO
    assert kind_for_relative_path("notes.md") is None


# Drag payload: encode then decode must return the original relative path (the drop target
# receives raw bytes from the Qt drag operation).
def test_asset_mime_payload_round_trip() -> None:
    payload = encode_asset_mime_payload("images/cover.png")
    assert decode_asset_mime_payload(payload) == "images/cover.png"


# An empty path would produce an empty drag payload that no drop target could interpret.
def test_encode_asset_mime_payload_rejects_empty_path() -> None:
    with pytest.raises(SecurityError):
        encode_asset_mime_payload("")


# Drop data comes from outside this code path (another widget or app), so malformed bytes must raise
# SecurityError instead of UnicodeDecodeError.
def test_decode_asset_mime_payload_rejects_invalid_utf8() -> None:
    with pytest.raises(SecurityError):
        decode_asset_mime_payload(b"\xff\xfe\xfd")


# Empty bytes carry no path and are rejected for the same reason.
def test_decode_asset_mime_payload_rejects_empty_payload() -> None:
    with pytest.raises(SecurityError):
        decode_asset_mime_payload(b"")
