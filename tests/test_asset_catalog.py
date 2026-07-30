from __future__ import annotations

from pathlib import Path

import pytest

from app.security import SecurityError
from app.ui.assets import AssetKind, build_asset_catalog
from app.ui.assets.catalog import MAX_ASSET_FILE_BYTES, MAX_ASSET_SCAN_FILES


def test_asset_catalog_groups_supported_local_files(tmp_path: Path) -> None:
    (tmp_path / "audio").mkdir()
    (tmp_path / "audio" / "kick.wav").write_bytes(b"RIFF")
    (tmp_path / "images").mkdir()
    (tmp_path / "images" / "cover.PNG").write_bytes(b"png")
    (tmp_path / "notes.md").write_text("unsupported", encoding="utf-8")

    catalog = build_asset_catalog(str(tmp_path))

    assert [entry.relative_path for entry in catalog.entries] == [
        "audio/kick.wav",
        "images/cover.PNG",
    ]
    assert [entry.name for entry in catalog.by_kind(AssetKind.AUDIO)] == ["kick.wav"]
    assert [entry.name for entry in catalog.by_kind(AssetKind.IMAGE)] == ["cover.PNG"]
    assert catalog.skipped_count == 1


def test_asset_catalog_rejects_non_directory_root(tmp_path: Path) -> None:
    asset = tmp_path / "asset.wav"
    asset.write_bytes(b"RIFF")

    with pytest.raises(SecurityError):
        build_asset_catalog(str(asset))


def test_asset_catalog_skips_oversized_supported_file(tmp_path: Path) -> None:
    large = tmp_path / "large.wav"
    with large.open("wb") as handle:
        handle.truncate(MAX_ASSET_FILE_BYTES + 1)

    catalog = build_asset_catalog(str(tmp_path))

    assert catalog.entries == ()
    assert catalog.skipped_count == 1


def test_asset_catalog_rejects_too_many_files(tmp_path: Path) -> None:
    for index in range(MAX_ASSET_SCAN_FILES + 1):
        (tmp_path / f"{index}.wav").write_bytes(b"")

    with pytest.raises(SecurityError, match="too many files"):
        build_asset_catalog(str(tmp_path))
