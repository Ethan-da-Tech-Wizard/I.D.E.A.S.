# ==============================================================================
# Program: catalog.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: Organizes project creative assets (textures, audio samples, 3D models, shaders, scripts) with search filtering, metadata tagging, and preview thumbnails.
# ==============================================================================
# How this module fits: it is the pure-Python, Qt-free half of the Asset Manager. app/ui/assets/asset_manager.py builds an AssetCatalog for the open workspace,
# shows it in a tree/list, and uses AssetEntry.kind to decide which editor opens a double-clicked file (MainWindow._route_asset_activation in app/application.py).
# It scans a folder tree, keeps only file types the app knows how to open, and refuses anything unsafe or too large. tests/test_asset_catalog.py covers it.
# Inputs: a root folder path. Outputs: an immutable AssetCatalog (root, entries sorted by path, count of skipped files) or SecurityError.
# Design decisions: a whitelist of nine extensions (anything else is ignored, never opened); limits on file size (50 MiB), number of files (2000), path length (240) and depth (16)
# keep a scan from running away on a huge or hostile folder; entries are frozen dataclasses so the UI cannot alter the catalog by accident; paths are stored relative to the root with
# forward slashes so project files are portable.
# Known limits: rglob("*") lists the whole tree and sorts it before the 2000-file cap is checked, so a gigantic folder costs time and memory before the error is raised; the cap
# counts every regular file, including ones with unsupported extensions; unreadable or oversized files are only counted in skipped_count (no per-file reason); file contents are never inspected,
# only the extension is trusted. StrEnum needs Python 3.11 or newer.

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from app.security import SecurityError


# Limits: 50 MiB per file, at most 2000 files examined per scan, relative paths up to 240 characters (inside common 255-byte filename limits) and at most 16 folders deep.
MAX_ASSET_FILE_BYTES = 50 * 1024 * 1024
MAX_ASSET_SCAN_FILES = 2_000
MAX_ASSET_PATH_CHARS = 240
MAX_ASSET_DEPTH = 16


class AssetKind(StrEnum):
    """Kind of asset, which decides which editor can open it. String values are stable and may be stored in project files."""
    AUDIO = "audio"
    IMAGE = "image"
    MODEL = "model"
    SCRIPT = "script"
    VIDEO = "video"


# Whitelist mapping lower-case extension -> AssetKind. .wav loads into the DAW; .png/.jpg/.jpeg into the photo editor; .mp4/.mov onto the video timeline; .obj is a 3D model for the engine;
# .py/.txt open in the code editor. Anything not listed (executables, archives, .svg...) is invisible to the app on purpose.
ALLOWED_ASSET_SUFFIXES: dict[str, AssetKind] = {
    ".wav": AssetKind.AUDIO,
    ".png": AssetKind.IMAGE,
    ".jpg": AssetKind.IMAGE,
    ".jpeg": AssetKind.IMAGE,
    ".obj": AssetKind.MODEL,
    ".py": AssetKind.SCRIPT,
    ".txt": AssetKind.SCRIPT,
    ".mp4": AssetKind.VIDEO,
    ".mov": AssetKind.VIDEO,
}


# Frozen (immutable) and slotted (compact) so thousands of entries stay cheap and cannot be modified after the scan.
@dataclass(frozen=True, slots=True)
class AssetEntry:
    """One catalogued file.

    Attributes:
        name: file name only, e.g. "kick.wav".
        relative_path: path from the catalog root with forward slashes, e.g. "audio/kick.wav".
        kind: AssetKind derived from the extension.
        size_bytes: size on disk in bytes.
    """
    name: str
    relative_path: str
    kind: AssetKind
    size_bytes: int


# Immutable result of a scan.
@dataclass(frozen=True, slots=True)
class AssetCatalog:
    """Result of scanning a folder.

    Attributes:
        root: absolute, symlink-resolved path of the scanned folder.
        entries: accepted files, sorted by path.
        skipped_count: files that were rejected (bad path, unreadable, too large) and left out.
    """
    root: str
    entries: tuple[AssetEntry, ...]
    skipped_count: int = 0

    def by_kind(self, kind: AssetKind) -> tuple[AssetEntry, ...]:
        """Return the entries of one kind, in catalog order.

        Args:
            kind: AssetKind to filter by.
        """
        return tuple(entry for entry in self.entries if entry.kind == kind)


def _validate_asset_relative_path(relative_path: str) -> str:
    """Check that a relative asset path is safe and supported, and normalise it.

    Args:
        relative_path: path as text, relative to the catalog root.

    Returns:
        The path rewritten with forward slashes and no redundant parts.

    Raises:
        SecurityError: not text or empty, too long, absolute, too deep, contains "", "." or ".." parts, or has an unsupported extension.
    """
    if not isinstance(relative_path, str) or not relative_path:
        raise SecurityError("Asset path must be text.")
    if len(relative_path) > MAX_ASSET_PATH_CHARS:
        raise SecurityError("Asset path is too long.")
    # Absolute paths would ignore the root when joined, so they are refused up front.
    if Path(relative_path).is_absolute():
        raise SecurityError("Asset paths must be relative.")
    parts = Path(relative_path).parts
    if len(parts) > MAX_ASSET_DEPTH:
        raise SecurityError("Asset path is too deep.")
    # Reject "." and ".." parts (traversal) and empty parts; this runs before anything touches the filesystem.
    if any(part in {"", ".", ".."} for part in parts):
        raise SecurityError("Asset path cannot contain traversal.")
    suffix = Path(relative_path).suffix.lower()
    if suffix not in ALLOWED_ASSET_SUFFIXES:
        raise SecurityError("Asset type is not supported.")
    return Path(*parts).as_posix()


def _resolve_asset(root: Path, relative_path: str) -> Path:
    """Turn a validated relative path into a real path and verify it stays inside the root.

    Args:
        root: resolved catalog root.
        relative_path: path to check (validated again here).

    Returns:
        Resolved absolute path.

    Raises:
        SecurityError: validation fails, or a symlink makes the resolved path leave the root.
    """
    clean_relative_path = _validate_asset_relative_path(relative_path)
    # resolve() follows symlinks; comparing against the root afterwards is what actually catches a link that points outside the tree (the text-only checks cannot).
    candidate = root.joinpath(clean_relative_path).resolve(strict=False)
    if root != candidate and root not in candidate.parents:
        raise SecurityError("Asset path escapes the catalog root.")
    return candidate


def build_asset_catalog(root_path: str) -> AssetCatalog:
    """Scan a folder and list the supported assets in it.

    Args:
        root_path: folder to scan ("~" is expanded). Must exist.

    Returns:
        AssetCatalog with entries sorted by relative path.

    Raises:
        SecurityError: root is not a directory, or more than MAX_ASSET_SCAN_FILES files are found.
        FileNotFoundError: the root does not exist (resolve(strict=True) is not converted).
    """
    # Resolve once so every later containment comparison uses the same canonical root.
    root = Path(root_path).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise SecurityError("Asset catalog root must be a directory.")

    entries: list[AssetEntry] = []
    skipped_count = 0
    seen_files = 0

    # sorted() makes the order deterministic across operating systems, which keeps the UI and tests stable.
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        seen_files += 1
        # Fail the whole scan rather than silently truncating: the user should know the folder is too big to catalogue.
        if seen_files > MAX_ASSET_SCAN_FILES:
            raise SecurityError("Asset catalog contains too many files.")

        # Validate the path twice on purpose (text rules, then filesystem containment); a file that fails either is skipped, not fatal.
        relative_path = path.relative_to(root).as_posix()
        try:
            clean_relative_path = _validate_asset_relative_path(relative_path)
            resolved = _resolve_asset(root, clean_relative_path)
            stat = resolved.stat()
            if stat.st_size > MAX_ASSET_FILE_BYTES:
                skipped_count += 1
                continue
            kind = ALLOWED_ASSET_SUFFIXES[resolved.suffix.lower()]
        # A file that vanished or is unreadable (OSError) or breaks a rule counts as skipped; the scan keeps going.
        except (OSError, SecurityError):
            skipped_count += 1
            continue

        entries.append(
            AssetEntry(
                name=resolved.name,
                relative_path=clean_relative_path,
                kind=kind,
                size_bytes=stat.st_size,
            )
        )

    return AssetCatalog(root=str(root), entries=tuple(entries), skipped_count=skipped_count)
