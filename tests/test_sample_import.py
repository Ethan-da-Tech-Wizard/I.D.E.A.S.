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


def write_valid_wav(path: Path, n_frames: int = 100) -> None:
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(44100)
        wav_file.writeframes(b"\x00\x00" * n_frames)


def make_workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


def test_import_custom_sample_happy_path(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    source = tmp_path / "kick.wav"
    write_valid_wav(source)

    relative_path = import_custom_sample(str(ws), str(source))

    assert relative_path == "custom/kick.wav"
    copied = ws / "audio" / "custom" / "kick.wav"
    assert copied.exists()
    # Import is a copy: mutating the original does not affect the copy.
    assert copied.read_bytes() == source.read_bytes()


def test_import_custom_sample_then_resolve_and_list(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    source = tmp_path / "snare.wav"
    write_valid_wav(source)

    relative_path = import_custom_sample(str(ws), str(source))
    resolved = resolve_custom_sample_path(str(ws), relative_path)

    assert Path(resolved).is_file()
    assert list_custom_samples(str(ws)) == ["custom/snare.wav"]


def test_import_custom_sample_rejects_non_wav_suffix(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    source = tmp_path / "notes.txt"
    source.write_text("not audio")

    with pytest.raises(SecurityError, match="\\.wav"):
        import_custom_sample(str(ws), str(source))


def test_import_custom_sample_rejects_oversized_file(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    source = tmp_path / "huge.wav"
    write_valid_wav(source)
    with source.open("ab") as handle:
        handle.truncate(MAX_CUSTOM_SAMPLE_BYTES + 1)

    with pytest.raises(SecurityError, match="too large"):
        import_custom_sample(str(ws), str(source))


def test_import_custom_sample_rejects_corrupt_wav_bytes(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    source = tmp_path / "fake.wav"
    source.write_bytes(b"this is not a real wav file at all, just junk bytes")

    with pytest.raises(SecurityError, match="not a valid WAV"):
        import_custom_sample(str(ws), str(source))


def test_import_custom_sample_rejects_missing_file(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)

    with pytest.raises(SecurityError, match="regular file"):
        import_custom_sample(str(ws), str(tmp_path / "does_not_exist.wav"))


def test_import_custom_sample_sanitizes_unsafe_filename(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    source = tmp_path / "weird name!@#.wav"
    write_valid_wav(source)

    relative_path = import_custom_sample(str(ws), str(source))

    assert relative_path == "custom/weird_name___.wav"


def test_import_custom_sample_avoids_name_collision(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    source = tmp_path / "kick.wav"
    write_valid_wav(source)

    first = import_custom_sample(str(ws), str(source))
    second = import_custom_sample(str(ws), str(source))

    assert first == "custom/kick.wav"
    assert second == "custom/kick_1.wav"
    assert (ws / "audio" / "custom" / "kick_1.wav").exists()


def test_resolve_custom_sample_path_rejects_traversal(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    with pytest.raises(SecurityError, match="traversal"):
        resolve_custom_sample_path(str(ws), "../../etc/passwd.wav")


def test_resolve_custom_sample_path_rejects_absolute_path(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    with pytest.raises(SecurityError, match="relative path"):
        resolve_custom_sample_path(str(ws), "/etc/passwd.wav")


def test_resolve_custom_sample_path_rejects_non_wav_suffix(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    with pytest.raises(SecurityError, match="\\.wav"):
        resolve_custom_sample_path(str(ws), "custom/script.py")


def test_resolve_custom_sample_path_rejects_missing_file(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    with pytest.raises(SecurityError, match="not found"):
        resolve_custom_sample_path(str(ws), "custom/missing.wav")


def test_list_custom_samples_empty_when_none_imported(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    assert list_custom_samples(str(ws)) == []
