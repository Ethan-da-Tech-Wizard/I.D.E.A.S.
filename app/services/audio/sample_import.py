# ==============================================================================
# Program: sample_import.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: Imports, decodes, and caches external audio sample files, detecting transients, calculating root pitches, and preparing PCM wave data for sampler instruments.
# ==============================================================================
# How this module fits: the DAW's "import sample" button (app/ui/daw.py, via MainWindow._set_current_workspace which supplies the workspace folder) calls
# import_custom_sample() to copy a user-chosen WAV into <workspace>/audio/custom/, and later list_custom_samples() to fill the sample picker and
# resolve_custom_sample_path() to get the real file to play. The bundled sample library (app/resources/samples) is a separate, equally strict boundary
# handled by app/security.py. tests/test_sample_import.py covers these functions.
# Inputs: a workspace folder and a user-selected source file. Outputs: a relative path like "custom/kick.wav" (always forward slash, for stable project files).
# Design decisions: the file is always COPIED (the project keeps working if the original moves); the name is reduced to letters, digits, "_", "." and "-";
# the file must be a real PCM WAV with at least one frame and at most 50 MiB; existing files are never overwritten (a numeric suffix is added).
# Known limits: only .wav; the check-then-copy sequence is not atomic (another process could create the same name in between); stat/copy follow symlinks,
# so a symlinked source is read through; no sample-rate or channel validation beyond what the wave module accepts.

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

# 50 MiB cap: roughly five minutes of 16-bit stereo audio at 44.1 kHz, plenty for a sample and small enough that a copy cannot fill the disk quickly.
MAX_CUSTOM_SAMPLE_BYTES = 50 * 1024 * 1024
CUSTOM_SAMPLES_SUBDIR = "custom"
# Anything outside this whitelist (spaces, path separators, quotes, non-ASCII) becomes "_", so the stored file name is safe on every operating system and in any later shell or path use.
_UNSAFE_NAME_CHARS = re.compile(r"[^A-Za-z0-9_.-]")


def _sanitize_filename(name: str) -> str:
    """Turn a user's file name into a safe ".wav" name.

    Args:
        name: base name from the source path (not trusted).

    Returns:
        "<safe stem>.wav", with the stem limited to 200 characters and defaulting to "sample".
    """
    # Take only the stem (drop the extension and any directory remnants); the extension is forced to .wav below.
    stem = Path(name).stem or "sample"
    safe_stem = _UNSAFE_NAME_CHARS.sub("_", stem)[:200] or "sample"
    return f"{safe_stem}.wav"


def _validate_wav_file(source_path: str) -> None:
    """Check that a file is an importable WAV.

    Args:
        source_path: path chosen by the user.

    Raises:
        SecurityError: not a regular file, not a .wav name, empty or over 50 MiB, not parseable as WAV, or contains no audio frames.
    """
    path = Path(source_path)
    # Reject directories, devices and missing paths (a device such as /dev/zero would otherwise be read forever).
    if not path.is_file():
        raise SecurityError("Sample source must be a regular file.")
    # Cheap name check first; the real format check is the header parse further down.
    if path.suffix.lower() != ".wav":
        raise SecurityError("Only .wav files can be imported as samples.")
    size = path.stat().st_size
    if size <= 0 or size > MAX_CUSTOM_SAMPLE_BYTES:
        raise SecurityError("Sample file is empty or too large to import.")
    # wave.open reads and validates the RIFF header; it raises for non-WAV content even when the file is named .wav.
    try:
        with wave.open(str(path), "rb") as wav_file:
            # A header-only file is useless as a sample and would make the engine's length maths misbehave.
            if wav_file.getnframes() <= 0:
                raise SecurityError("Sample file has no audio frames.")
    except (wave.Error, EOFError) as exc:
        raise SecurityError("File is not a valid WAV file.") from exc


def import_custom_sample(workspace_path: str, source_path: str) -> str:
    """Validate a WAV and copy it into the workspace's custom sample folder.

    Args:
        workspace_path: workspace root.
        source_path: file to import.

    Returns:
        Relative path such as "custom/kick_v2.wav", suitable for resolve_custom_sample_path().

    Raises:
        SecurityError: validation fails, or a computed path would escape the workspace. An invalid file is never partially copied.
        OSError: the copy itself fails (disk full, permissions).
    """
    # Validate before creating any folder, so a refused import leaves the workspace untouched.
    _validate_wav_file(source_path)

    # get_workspace_dir only accepts known categories ("audio" here), which stops callers from choosing arbitrary folders.
    audio_dir = get_workspace_dir(workspace_path, "audio")
    custom_dir = os.path.join(audio_dir, CUSTOM_SAMPLES_SUBDIR)
    # Defence in depth: the containment check is repeated on the final folder and on the destination file name.
    if not is_safe_subpath(workspace_path, custom_dir):
        raise SecurityError("Custom samples directory escapes the workspace.")
    os.makedirs(custom_dir, exist_ok=True)

    safe_name = _sanitize_filename(os.path.basename(source_path))
    destination = os.path.join(custom_dir, safe_name)
    if not is_safe_subpath(workspace_path, destination):
        raise SecurityError("Destination path escapes the workspace.")

    # Avoid clobbering an existing sample that happens to sanitize to the same name.
    # Avoid clobbering an existing sample that happens to sanitize to the same name.
    counter = 1
    stem = Path(destination).stem
    while os.path.exists(destination):
        destination = os.path.join(custom_dir, f"{stem}_{counter}.wav")
        counter += 1

    # copyfile copies content only (no permissions or metadata from the source).
    shutil.copyfile(source_path, destination)
    # Forward slash on purpose, whatever the operating system, so project files stay portable.
    return f"{CUSTOM_SAMPLES_SUBDIR}/{os.path.basename(destination)}"


def resolve_custom_sample_path(workspace_path: str, relative_path: str) -> str:
    """Turn a relative custom-sample path into an existing absolute file path, rejecting anything unsafe.

    Args:
        workspace_path: workspace root.
        relative_path: e.g. "custom/kick.wav" as returned by import_custom_sample.

    Returns:
        Absolute path of the file.

    Raises:
        SecurityError: not relative text, contains "." / ".." parts, wrong extension, escapes the audio folder, or does not exist.
    """
    # Reject absolute paths first: joining one onto the audio folder would silently discard the folder.
    if not isinstance(relative_path, str) or not relative_path or os.path.isabs(relative_path):
        raise SecurityError("Custom sample path must be a relative path.")
    parts = Path(relative_path).parts
    # Explicit traversal check on each path component, before the filesystem resolves anything.
    if any(part in {"", ".", ".."} for part in parts):
        raise SecurityError("Custom sample path cannot contain traversal.")
    if Path(relative_path).suffix.lower() != ".wav":
        raise SecurityError("Only .wav custom samples can be played.")

    audio_dir = get_workspace_dir(workspace_path, "audio")
    # resolve() follows symlinks, so a symlink inside the audio folder that points elsewhere is detected by the containment test below.
    candidate = Path(audio_dir).joinpath(relative_path).resolve(strict=False)
    root = Path(audio_dir).resolve(strict=False)
    # Accept only the folder itself or something under it.
    if root != candidate and root not in candidate.parents:
        raise SecurityError("Custom sample path escapes the workspace audio directory.")
    # Last check: the sample must actually exist as a regular file.
    if not candidate.is_file():
        raise SecurityError("Custom sample was not found.")
    return str(candidate)


def list_custom_samples(workspace_path: str) -> list[str]:
    """List the custom samples available in a workspace.

    Returns:
        Sorted relative paths like "custom/kick.wav"; [] if the custom folder does not exist yet. Only files ending in .wav are listed.
    """
    audio_dir = get_workspace_dir(workspace_path, "audio")
    custom_dir = os.path.join(audio_dir, CUSTOM_SAMPLES_SUBDIR)
    if not os.path.isdir(custom_dir):
        return []
    names = []
    # sorted() makes the picker order stable across runs and platforms.
    for entry in sorted(os.listdir(custom_dir)):
        candidate = os.path.join(custom_dir, entry)
        # Skip subfolders and non-WAV files that a user may have dropped into the folder by hand.
        if entry.lower().endswith(".wav") and os.path.isfile(candidate):
            names.append(f"{CUSTOM_SAMPLES_SUBDIR}/{entry}")
    return names
