from __future__ import annotations

import math
import wave
from array import array
from pathlib import Path

import pytest

from app.services.audio_export import ExportTrack, export_pattern_wav
from app.security import SecurityError


def write_test_tone(path: Path, seconds: float = 0.05, freq: float = 440.0, rate: int = 44100) -> None:
    n = int(rate * seconds)
    samples = array("h", (int(0.25 * 32767 * math.sin(2 * math.pi * freq * i / rate)) for i in range(n)))
    with wave.open(str(path), "w") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(rate)
        wav_file.writeframes(samples.tobytes())


def test_export_pattern_wav_writes_bounded_pcm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ID_APP_DATA_DIR", str(tmp_path / "data"))
    samples_root = tmp_path / "samples"
    samples_root.mkdir()
    write_test_tone(samples_root / "tone.wav")

    output = export_pattern_wav(
        str(tmp_path / "out"),
        str(samples_root),
        120,
        [ExportTrack("tone.wav", [True] + [False] * 15)],
    )

    assert output.endswith(".wav")
    with wave.open(output, "rb") as check:
        assert check.getframerate() == 44100
        assert check.getnchannels() == 1
        assert check.getnframes() > 0


def test_export_pattern_wav_blocks_bad_bpm(tmp_path: Path) -> None:
    samples_root = tmp_path / "samples"
    samples_root.mkdir()

    with pytest.raises(SecurityError):
        export_pattern_wav(str(tmp_path / "out.wav"), str(samples_root), 1000, [])
