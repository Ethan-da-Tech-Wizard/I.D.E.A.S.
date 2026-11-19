# ==============================================================================
# Program: tests/test_audio_export.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: tests app/services/audio_export.py (offline WAV bounce of a step pattern): the output is a
# valid mono 44.1 kHz 16-bit WAV, and absurd tempos are rejected before any work is done.
# Samples are generated in the tests, so nothing depends on bundled audio files.
# Run: python3 -m pytest tests/test_audio_export.py -q
# ==============================================================================

from __future__ import annotations

import math
import wave
from array import array
from pathlib import Path

import pytest

from app.services.audio_export import ExportTrack, export_pattern_wav
from app.security import SecurityError


# Test helper: write a short mono 16-bit sine WAV. Amplitude 0.25 of full scale keeps headroom;
# 50 ms is enough for the exporter to read and mix while staying fast.
def write_test_tone(path: Path, seconds: float = 0.05, freq: float = 440.0, rate: int = 44100) -> None:
    """Create a sine-wave WAV at `path` (defaults: 0.05 s, 440 Hz, 44.1 kHz)."""
    # Sample count = rate x duration; the sine phase is 2*pi*freq*i/rate radians for sample i.
    n = int(rate * seconds)
    samples = array("h", (int(0.25 * 32767 * math.sin(2 * math.pi * freq * i / rate)) for i in range(n)))
    with wave.open(str(path), "w") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(rate)
        wav_file.writeframes(samples.tobytes())


# Scenario: one track with a hit on the first of 16 steps at 120 BPM. The result must be a readable
# WAV with the standard export format (44.1 kHz, mono) and at least one frame.
def test_export_pattern_wav_writes_bounded_pcm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Redirect the app data directory so the exporter's output-location rules use a private temp folder.
    monkeypatch.setenv("ID_APP_DATA_DIR", str(tmp_path / "data"))
    samples_root = tmp_path / "samples"
    samples_root.mkdir()
    write_test_tone(samples_root / "tone.wav")

    # Arguments: output folder, samples root, BPM, and the track list (sample name + 16 step booleans).
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


# Scenario: BPM 1000 is outside the allowed range. Validation must raise SecurityError up front, so
# a bad tempo cannot produce an enormous or degenerate render. The empty track list proves the
# tempo check runs before track handling.
def test_export_pattern_wav_blocks_bad_bpm(tmp_path: Path) -> None:
    samples_root = tmp_path / "samples"
    samples_root.mkdir()

    with pytest.raises(SecurityError):
        export_pattern_wav(str(tmp_path / "out.wav"), str(samples_root), 1000, [])
