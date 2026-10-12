from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from app.security import SecurityError


MAX_ASSET_FILE_BYTES = 50 * 1024 * 1024
MAX_ASSET_SCAN_FILES = 2_000
MAX_ASSET_PATH_CHARS = 240
MAX_ASSET_DEPTH = 16


class AssetKind(StrEnum):
    AUDIO = "audio"
    IMAGE = "image"
    MODEL = "model"
    SCRIPT = "script"
    VIDEO = "video"


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


@dataclass(frozen=True, slots=True)
class AssetEntry:
    name: str
    relative_path: str
    kind: AssetKind
    size_bytes: int


@dataclass(frozen=True, slots=True)
class AssetCatalog:
    root: str
    entries: tuple[AssetEntry, ...]
    skipped_count: int = 0

    def by_kind(self, kind: AssetKind) -> tuple[AssetEntry, ...]:
        return tuple(entry for entry in self.entries if entry.kind == kind)


def _validate_asset_relative_path(relative_path: str) -> str:
    if not isinstance(relative_path, str) or not relative_path:
        raise SecurityError("Asset path must be text.")
    if len(relative_path) > MAX_ASSET_PATH_CHARS:
        raise SecurityError("Asset path is too long.")
    if Path(relative_path).is_absolute():
        raise SecurityError("Asset paths must be relative.")
    parts = Path(relative_path).parts
    if len(parts) > MAX_ASSET_DEPTH:
        raise SecurityError("Asset path is too deep.")
    if any(part in {"", ".", ".."} for part in parts):
        raise SecurityError("Asset path cannot contain traversal.")
    suffix = Path(relative_path).suffix.lower()
    if suffix not in ALLOWED_ASSET_SUFFIXES:
        raise SecurityError("Asset type is not supported.")
    return Path(*parts).as_posix()


def _resolve_asset(root: Path, relative_path: str) -> Path:
    clean_relative_path = _validate_asset_relative_path(relative_path)
    candidate = root.joinpath(clean_relative_path).resolve(strict=False)
    if root != candidate and root not in candidate.parents:
        raise SecurityError("Asset path escapes the catalog root.")
    return candidate


def build_asset_catalog(root_path: str) -> AssetCatalog:
    root = Path(root_path).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise SecurityError("Asset catalog root must be a directory.")

    entries: list[AssetEntry] = []
    skipped_count = 0
    seen_files = 0

    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        seen_files += 1
        if seen_files > MAX_ASSET_SCAN_FILES:
            raise SecurityError("Asset catalog contains too many files.")

        relative_path = path.relative_to(root).as_posix()
        try:
            clean_relative_path = _validate_asset_relative_path(relative_path)
            resolved = _resolve_asset(root, clean_relative_path)
            stat = resolved.stat()
            if stat.st_size > MAX_ASSET_FILE_BYTES:
                skipped_count += 1
                continue
            kind = ALLOWED_ASSET_SUFFIXES[resolved.suffix.lower()]
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
