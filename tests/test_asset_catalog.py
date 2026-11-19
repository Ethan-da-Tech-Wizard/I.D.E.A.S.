# ==============================================================================
# Program: tests/test_asset_catalog.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: tests app/ui/assets/catalog.py, which scans a folder tree into an immutable catalog of
# supported media files (audio, image, video) for the asset manager. Checks grouping, extension
# case-insensitivity, skipping of unsupported/oversized files and the scan-size safety limits.
# Run: python3 -m pytest tests/test_asset_catalog.py -q
# ==============================================================================

from __future__ import annotations

from pathlib import Path

import pytest

from app.security import SecurityError
from app.ui.assets import AssetKind, build_asset_catalog
from app.ui.assets.catalog import MAX_ASSET_FILE_BYTES, MAX_ASSET_SCAN_FILES


# Scenario: a mini project with audio, an image with an upper-case extension, and an unsupported
# note. Pins: entries come back in sorted relative-path order with posix separators, ".PNG" is still
# recognised, and the unsupported file is counted in skipped_count instead of being listed.
def test_asset_catalog_groups_supported_local_files(tmp_path: Path) -> None:
    (tmp_path / "audio").mkdir()
    (tmp_path / "audio" / "kick.wav").write_bytes(b"RIFF")
    (tmp_path / "images").mkdir()
    (tmp_path / "images" / "cover.PNG").write_bytes(b"png")
    (tmp_path / "notes.md").write_text("unsupported", encoding="utf-8")

    # build_asset_catalog takes a string path (the UI passes the workspace path as text).
    catalog = build_asset_catalog(str(tmp_path))

    assert [entry.relative_path for entry in catalog.entries] == [
        "audio/kick.wav",
        "images/cover.PNG",
    ]
    assert [entry.name for entry in catalog.by_kind(AssetKind.AUDIO)] == ["kick.wav"]
    assert [entry.name for entry in catalog.by_kind(AssetKind.IMAGE)] == ["cover.PNG"]
    assert catalog.skipped_count == 1


# Scenario: video containers (.mp4 and an upper-case .MOV) are classified as VIDEO. Sorted before
# comparing because the test does not care about scan order here.
def test_asset_catalog_groups_video_clips(tmp_path: Path) -> None:
    (tmp_path / "clips").mkdir()
    (tmp_path / "clips" / "intro.mp4").write_bytes(b"ftyp")
    (tmp_path / "clips" / "outro.MOV").write_bytes(b"ftyp")

    catalog = build_asset_catalog(str(tmp_path))

    assert sorted(e.name for e in catalog.by_kind(AssetKind.VIDEO)) == ["intro.mp4", "outro.MOV"]


# Scenario: the root points at a file. A catalog must only be built for folders, so SecurityError
# is raised rather than silently scanning nothing.
def test_asset_catalog_rejects_non_directory_root(tmp_path: Path) -> None:
    asset = tmp_path / "asset.wav"
    asset.write_bytes(b"RIFF")

    with pytest.raises(SecurityError):
        build_asset_catalog(str(asset))


# Scenario: a .wav one byte over MAX_ASSET_FILE_BYTES. The file is skipped (counted, not listed) so a
# huge file cannot be pulled into the UI. truncate() makes a sparse file, so no real disk is used.
def test_asset_catalog_skips_oversized_supported_file(tmp_path: Path) -> None:
    large = tmp_path / "large.wav"
    with large.open("wb") as handle:
        handle.truncate(MAX_ASSET_FILE_BYTES + 1)

    catalog = build_asset_catalog(str(tmp_path))

    assert catalog.entries == ()
    assert catalog.skipped_count == 1


# Scenario: MAX_ASSET_SCAN_FILES + 1 files. The scan aborts with "too many files": a safety valve
# against pointing the asset manager at a huge directory and freezing the UI. Empty files keep the
# test fast.
def test_asset_catalog_rejects_too_many_files(tmp_path: Path) -> None:
    for index in range(MAX_ASSET_SCAN_FILES + 1):
        (tmp_path / f"{index}.wav").write_bytes(b"")

    with pytest.raises(SecurityError, match="too many files"):
        build_asset_catalog(str(tmp_path))
