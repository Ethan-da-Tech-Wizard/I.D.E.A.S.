from __future__ import annotations

from pathlib import Path

import pytest

from app.storage.database import init_db
from app.security import (
    SecurityError,
    list_bundled_samples,
    parse_safe_script,
    read_text_file_safely,
    resolve_sample_path,
    run_safe_script,
)


def test_safe_script_allows_only_literal_play_sample_calls() -> None:
    assert parse_safe_script("play_sample('sfx/impact.wav')\nplay_sample(\"sfx/riser.wav\")") == [
        "sfx/impact.wav",
        "sfx/riser.wav",
    ]


@pytest.mark.parametrize(
    "code",
    [
        "import os\nos.remove('x')",
        "__import__('os').system('shutdown now')",
        "open('/tmp/x', 'w')",
        "while True:\n    pass",
        "play_sample('../../secret.wav')",
    ],
)
def test_safe_script_blocks_malicious_python(code: str) -> None:
    played: list[str] = []
    with pytest.raises(SecurityError):
        run_safe_script(code, played.append)
    assert played == []


def test_resolve_sample_path_blocks_escape(tmp_path: Path) -> None:
    samples = tmp_path / "samples"
    samples.mkdir()
    (samples / "ok.wav").write_bytes(b"RIFF")

    assert resolve_sample_path(str(samples), "ok.wav") == str(samples / "ok.wav")
    with pytest.raises(SecurityError):
        resolve_sample_path(str(samples), "../secret.wav")


def test_list_bundled_samples_returns_only_safe_wavs(tmp_path: Path) -> None:
    samples = tmp_path / "samples"
    samples.mkdir()
    (samples / "ok.wav").write_bytes(b"RIFF")
    (samples / "skip.mp3").write_bytes(b"ID3")

    nested = samples / "nested"
    nested.mkdir()
    (nested / "also-ok.wav").write_bytes(b"RIFF")

    assert list_bundled_samples(str(samples)) == ["nested/also-ok.wav", "ok.wav"]


def test_read_text_file_safely_blocks_binary_and_large_files(tmp_path: Path) -> None:
    text = tmp_path / "ok.py"
    binary = tmp_path / "bad.bin"
    large = tmp_path / "large.txt"

    text.write_text("print('safe to view')", encoding="utf-8")
    binary.write_bytes(b"abc\x00def")
    large.write_text("x" * 11, encoding="utf-8")

    assert read_text_file_safely(str(text), max_bytes=10_000) == "print('safe to view')"
    with pytest.raises(SecurityError):
        read_text_file_safely(str(binary), max_bytes=10_000)
    with pytest.raises(SecurityError):
        read_text_file_safely(str(large), max_bytes=10)


def test_init_db_uses_private_permissions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ID_APP_DATA_DIR", str(tmp_path / "data"))

    init_db()
    db_file = tmp_path / "data" / "id_app.db"
    assert db_file.is_file()
    assert db_file.stat().st_mode & 0o777 == 0o600
    assert db_file.parent.stat().st_mode & 0o777 == 0o700


def test_safe_script_never_executes_python_file_write() -> None:
    played: list[str] = []
    with pytest.raises(SecurityError):
        run_safe_script("open('/tmp/owned', 'w').write('x')", played.append)
    assert played == []
