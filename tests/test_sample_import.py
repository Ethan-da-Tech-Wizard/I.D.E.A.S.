# ==============================================================================
# Program: tests/test_sample_import.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: tests app/services/audio/sample_import.py: copying a user-chosen WAV into the workspace's
# audio/custom/ folder and resolving or listing those samples safely. Checks validation (suffix,
# size, real WAV header, regular file), file-name sanitising, name-collision handling and the
# path-traversal guards used when a saved pattern refers back to a custom sample.
# Run: python3 -m pytest tests/test_sample_import.py -q
# ==============================================================================

from __future__ import annotations

import wave
from pathlib import Path

import pytest

from app.security import SecurityError
from app.services.audio.sample_import import (
    MAX_CUSTOM_SAMPLE_BYTES,
    import_custom_sample,
    list_custom_samples,
    resolve_custom_sample_path,
)


# Helper: a tiny but valid mono 16-bit 44.1 kHz WAV of silence.
def write_valid_wav(path: Path, n_frames: int = 100) -> None:
    """Write `n_frames` frames of silence to `path`."""
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(44100)
        wav_file.writeframes(b"\x00\x00" * n_frames)


# Helper: an empty workspace folder (audio/custom/ is created by the importer).
def make_workspace(tmp_path: Path) -> Path:
    """Create and return tmp_path/"workspace"."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


# Import returns the workspace-relative name used in saved patterns ("custom/kick.wav") and copies the bytes.
def test_import_custom_sample_happy_path(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    source = tmp_path / "kick.wav"
    write_valid_wav(source)

    relative_path = import_custom_sample(str(ws), str(source))

    assert relative_path == "custom/kick.wav"
    copied = ws / "audio" / "custom" / "kick.wav"
    assert copied.exists()
    # The original stays where it is; later edits to it do not change the project's copy.
    # Import is a copy: mutating the original does not affect the copy.
    assert copied.read_bytes() == source.read_bytes()


# The returned relative path resolves to a real file and appears in the list of custom samples.
def test_import_custom_sample_then_resolve_and_list(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    source = tmp_path / "snare.wav"
    write_valid_wav(source)

    relative_path = import_custom_sample(str(ws), str(source))
    resolved = resolve_custom_sample_path(str(ws), relative_path)

    assert Path(resolved).is_file()
    assert list_custom_samples(str(ws)) == ["custom/snare.wav"]


# Only .wav files are accepted, which keeps arbitrary files out of the sample folder.
def test_import_custom_sample_rejects_non_wav_suffix(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    source = tmp_path / "notes.txt"
    source.write_text("not audio")

    with pytest.raises(SecurityError, match="\\.wav"):
        import_custom_sample(str(ws), str(source))


# Files over MAX_CUSTOM_SAMPLE_BYTES are refused; truncate() extends a valid WAV to the limit + 1 byte cheaply (sparse).
def test_import_custom_sample_rejects_oversized_file(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    source = tmp_path / "huge.wav"
    write_valid_wav(source)
    with source.open("ab") as handle:
        handle.truncate(MAX_CUSTOM_SAMPLE_BYTES + 1)

    with pytest.raises(SecurityError, match="too large"):
        import_custom_sample(str(ws), str(source))


# A ".wav" name is not enough: the header is parsed, and junk bytes are rejected as "not a valid WAV".
def test_import_custom_sample_rejects_corrupt_wav_bytes(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    source = tmp_path / "fake.wav"
    source.write_bytes(b"this is not a real wav file at all, just junk bytes")

    with pytest.raises(SecurityError, match="not a valid WAV"):
        import_custom_sample(str(ws), str(source))


# A path that is not a regular file (missing, directory, device) is refused.
def test_import_custom_sample_rejects_missing_file(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)

    with pytest.raises(SecurityError, match="regular file"):
        import_custom_sample(str(ws), str(tmp_path / "does_not_exist.wav"))


# Characters outside a safe set become underscores ("weird name!@#" -> "weird_name___"), so the
# stored name is safe on every OS and in saved JSON.
def test_import_custom_sample_sanitizes_unsafe_filename(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    source = tmp_path / "weird name!@#.wav"
    write_valid_wav(source)

    relative_path = import_custom_sample(str(ws), str(source))

    assert relative_path == "custom/weird_name___.wav"


# Importing the same name twice never overwrites: the second copy gets a "_1" suffix.
def test_import_custom_sample_avoids_name_collision(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    source = tmp_path / "kick.wav"
    write_valid_wav(source)

    first = import_custom_sample(str(ws), str(source))
    second = import_custom_sample(str(ws), str(source))

    assert first == "custom/kick.wav"
    assert second == "custom/kick_1.wav"
    assert (ws / "audio" / "custom" / "kick_1.wav").exists()


# Saved patterns reference samples by relative path, which is untrusted when loading; ".." that escapes the folder is refused.
def test_resolve_custom_sample_path_rejects_traversal(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    with pytest.raises(SecurityError, match="traversal"):
        resolve_custom_sample_path(str(ws), "../../etc/passwd.wav")


# Absolute paths are rejected, only relative names are valid references.
def test_resolve_custom_sample_path_rejects_absolute_path(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    with pytest.raises(SecurityError, match="relative path"):
        resolve_custom_sample_path(str(ws), "/etc/passwd.wav")


# Even inside the folder, a non-.wav suffix (for example a script) is refused.
def test_resolve_custom_sample_path_rejects_non_wav_suffix(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    with pytest.raises(SecurityError, match="\\.wav"):
        resolve_custom_sample_path(str(ws), "custom/script.py")


# A well-formed reference to a file that no longer exists reports "not found".
def test_resolve_custom_sample_path_rejects_missing_file(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    with pytest.raises(SecurityError, match="not found"):
        resolve_custom_sample_path(str(ws), "custom/missing.wav")


# No custom folder yet means an empty list, not an error.
def test_list_custom_samples_empty_when_none_imported(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    assert list_custom_samples(str(ws)) == []
