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


def make_workspace(tmp_path: Path) -> Path:
    (tmp_path / "audio").mkdir()
    (tmp_path / "audio" / "kick.wav").write_bytes(b"RIFF")
    (tmp_path / "audio" / "snare.wav").write_bytes(b"RIFF")
    (tmp_path / "images").mkdir()
    (tmp_path / "images" / "cover.png").write_bytes(b"png")
    (tmp_path / "top_level.py").write_text("print('hi')", encoding="utf-8")
    return tmp_path


def test_asset_discovery_in_temp_directory(tmp_path: Path) -> None:
    make_workspace(tmp_path)
    model = AssetManagerModel(str(tmp_path))

    names = sorted(entry.name for entry in model.catalog.entries)
    assert names == ["cover.png", "kick.wav", "snare.wav", "top_level.py"]
    assert "" in model.folders()
    assert "audio" in model.folders()
    assert "images" in model.folders()


def test_filter_by_folder(tmp_path: Path) -> None:
    make_workspace(tmp_path)
    model = AssetManagerModel(str(tmp_path))

    audio_entries = model.filter_entries(folder="audio")
    assert sorted(e.name for e in audio_entries) == ["kick.wav", "snare.wav"]

    root_entries = model.filter_entries(folder="")
    assert [e.name for e in root_entries] == ["top_level.py"]


def test_filter_by_search_text(tmp_path: Path) -> None:
    make_workspace(tmp_path)
    model = AssetManagerModel(str(tmp_path))

    matches = model.filter_entries(search_text="kick")
    assert [e.name for e in matches] == ["kick.wav"]

    assert model.filter_entries(search_text="nonexistent") == []


def test_filter_by_kind(tmp_path: Path) -> None:
    make_workspace(tmp_path)
    model = AssetManagerModel(str(tmp_path))

    audio_only = model.filter_entries(kind=AssetKind.AUDIO)
    assert sorted(e.name for e in audio_only) == ["kick.wav", "snare.wav"]

    image_only = model.filter_entries(kind=AssetKind.IMAGE)
    assert [e.name for e in image_only] == ["cover.png"]


def test_combined_filters(tmp_path: Path) -> None:
    make_workspace(tmp_path)
    model = AssetManagerModel(str(tmp_path))

    result = model.filter_entries(folder="audio", search_text="snare", kind=AssetKind.AUDIO)
    assert [e.name for e in result] == ["snare.wav"]


def test_rename_asset_happy_path(tmp_path: Path) -> None:
    make_workspace(tmp_path)
    model = AssetManagerModel(str(tmp_path))

    new_path = model.rename_asset("audio/kick.wav", "kick_v2.wav")
    assert new_path == "audio/kick_v2.wav"
    assert (tmp_path / "audio" / "kick_v2.wav").exists()
    assert not (tmp_path / "audio" / "kick.wav").exists()


def test_rename_asset_rejects_path_separators_in_new_name(tmp_path: Path) -> None:
    make_workspace(tmp_path)
    model = AssetManagerModel(str(tmp_path))

    with pytest.raises(SecurityError):
        model.rename_asset("audio/kick.wav", "../escaped.wav")


def test_rename_asset_rejects_collision(tmp_path: Path) -> None:
    make_workspace(tmp_path)
    model = AssetManagerModel(str(tmp_path))

    with pytest.raises(SecurityError):
        model.rename_asset("audio/kick.wav", "snare.wav")


def test_delete_asset_happy_path(tmp_path: Path) -> None:
    make_workspace(tmp_path)
    model = AssetManagerModel(str(tmp_path))

    model.delete_asset("audio/kick.wav")
    assert not (tmp_path / "audio" / "kick.wav").exists()


def test_delete_asset_rejects_path_traversal(tmp_path: Path) -> None:
    make_workspace(tmp_path)
    outside = tmp_path.parent / "outside_secret.txt"
    outside.write_text("do not delete me", encoding="utf-8")
    model = AssetManagerModel(str(tmp_path))

    try:
        with pytest.raises(SecurityError):
            model.delete_asset("../outside_secret.txt")
        assert outside.exists()
    finally:
        outside.unlink(missing_ok=True)


def test_delete_asset_rejects_nonexistent_file(tmp_path: Path) -> None:
    make_workspace(tmp_path)
    model = AssetManagerModel(str(tmp_path))

    with pytest.raises(SecurityError):
        model.delete_asset("audio/does_not_exist.wav")


def test_folder_disk_path_resolves_within_root(tmp_path: Path) -> None:
    make_workspace(tmp_path)
    model = AssetManagerModel(str(tmp_path))

    assert model.folder_disk_path("audio") == (tmp_path / "audio").resolve()
    assert model.folder_disk_path("") == Path(model.root_path)


def test_kind_for_relative_path_routes_by_suffix() -> None:
    assert kind_for_relative_path("audio/kick.wav") == AssetKind.AUDIO
    assert kind_for_relative_path("images/cover.PNG") == AssetKind.IMAGE
    assert kind_for_relative_path("clips/intro.mp4") == AssetKind.VIDEO
    assert kind_for_relative_path("clips/outro.MOV") == AssetKind.VIDEO
    assert kind_for_relative_path("notes.md") is None


def test_asset_mime_payload_round_trip() -> None:
    payload = encode_asset_mime_payload("images/cover.png")
    assert decode_asset_mime_payload(payload) == "images/cover.png"


def test_encode_asset_mime_payload_rejects_empty_path() -> None:
    with pytest.raises(SecurityError):
        encode_asset_mime_payload("")


def test_decode_asset_mime_payload_rejects_invalid_utf8() -> None:
    with pytest.raises(SecurityError):
        decode_asset_mime_payload(b"\xff\xfe\xfd")


def test_decode_asset_mime_payload_rejects_empty_payload() -> None:
    with pytest.raises(SecurityError):
        decode_asset_mime_payload(b"")
