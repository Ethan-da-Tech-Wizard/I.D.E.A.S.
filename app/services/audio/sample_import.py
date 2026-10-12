"""Validated import of user-selected WAV files into a workspace's custom sample folder.

This deliberately does NOT weaken the DAW's existing bundled-sample boundary
(app/resources/samples, guarded by app.security.resolve_sample_path()). It adds
a second, equally strict boundary scoped to <workspace>/audio/custom/, and
import is always a copy -- never a live reference to the user's original file.
See docs/THREAT_MODEL.md's "Custom DAW sample import" entry for the reviewed
threat model this implements.
"""
from __future__ import annotations

import os
import re
import shutil
import wave
from pathlib import Path

from app.security import SecurityError
from app.storage.workspace import get_workspace_dir, is_safe_subpath

MAX_CUSTOM_SAMPLE_BYTES = 50 * 1024 * 1024
CUSTOM_SAMPLES_SUBDIR = "custom"
_UNSAFE_NAME_CHARS = re.compile(r"[^A-Za-z0-9_.-]")


def _sanitize_filename(name: str) -> str:
    stem = Path(name).stem or "sample"
    safe_stem = _UNSAFE_NAME_CHARS.sub("_", stem)[:200] or "sample"
    return f"{safe_stem}.wav"


def _validate_wav_file(source_path: str) -> None:
    path = Path(source_path)
    if not path.is_file():
        raise SecurityError("Sample source must be a regular file.")
    if path.suffix.lower() != ".wav":
        raise SecurityError("Only .wav files can be imported as samples.")
    size = path.stat().st_size
    if size <= 0 or size > MAX_CUSTOM_SAMPLE_BYTES:
        raise SecurityError("Sample file is empty or too large to import.")
    try:
        with wave.open(str(path), "rb") as wav_file:
            if wav_file.getnframes() <= 0:
                raise SecurityError("Sample file has no audio frames.")
    except (wave.Error, EOFError) as exc:
        raise SecurityError("File is not a valid WAV file.") from exc


def import_custom_sample(workspace_path: str, source_path: str) -> str:
    """Validates and copies source_path into <workspace_path>/audio/custom/.

    Returns the relative path (e.g. "custom/kick_v2.wav") suitable for
    resolve_custom_sample_path(). Raises SecurityError for anything that
    fails validation; never partially copies an invalid file.
    """
    _validate_wav_file(source_path)

    audio_dir = get_workspace_dir(workspace_path, "audio")
    custom_dir = os.path.join(audio_dir, CUSTOM_SAMPLES_SUBDIR)
    if not is_safe_subpath(workspace_path, custom_dir):
        raise SecurityError("Custom samples directory escapes the workspace.")
    os.makedirs(custom_dir, exist_ok=True)

    safe_name = _sanitize_filename(os.path.basename(source_path))
    destination = os.path.join(custom_dir, safe_name)
    if not is_safe_subpath(workspace_path, destination):
        raise SecurityError("Destination path escapes the workspace.")

    # Avoid clobbering an existing sample that happens to sanitize to the same name.
    counter = 1
    stem = Path(destination).stem
    while os.path.exists(destination):
        destination = os.path.join(custom_dir, f"{stem}_{counter}.wav")
        counter += 1

    shutil.copyfile(source_path, destination)
    return f"{CUSTOM_SAMPLES_SUBDIR}/{os.path.basename(destination)}"


def resolve_custom_sample_path(workspace_path: str, relative_path: str) -> str:
    """Mirrors app.security.resolve_sample_path()'s validation, scoped to the
    workspace's audio/custom/ directory instead of the bundled samples root."""
    if not isinstance(relative_path, str) or not relative_path or os.path.isabs(relative_path):
        raise SecurityError("Custom sample path must be a relative path.")
    parts = Path(relative_path).parts
    if any(part in {"", ".", ".."} for part in parts):
        raise SecurityError("Custom sample path cannot contain traversal.")
    if Path(relative_path).suffix.lower() != ".wav":
        raise SecurityError("Only .wav custom samples can be played.")

    audio_dir = get_workspace_dir(workspace_path, "audio")
    candidate = Path(audio_dir).joinpath(relative_path).resolve(strict=False)
    root = Path(audio_dir).resolve(strict=False)
    if root != candidate and root not in candidate.parents:
        raise SecurityError("Custom sample path escapes the workspace audio directory.")
    if not candidate.is_file():
        raise SecurityError("Custom sample was not found.")
    return str(candidate)


def list_custom_samples(workspace_path: str) -> list[str]:
    """Relative paths (e.g. "custom/kick.wav") of custom samples available in
    the workspace, or an empty list if none have been imported yet."""
    audio_dir = get_workspace_dir(workspace_path, "audio")
    custom_dir = os.path.join(audio_dir, CUSTOM_SAMPLES_SUBDIR)
    if not os.path.isdir(custom_dir):
        return []
    names = []
    for entry in sorted(os.listdir(custom_dir)):
        candidate = os.path.join(custom_dir, entry)
        if entry.lower().endswith(".wav") and os.path.isfile(candidate):
            names.append(f"{CUSTOM_SAMPLES_SUBDIR}/{entry}")
    return names
