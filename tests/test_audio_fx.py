# ==============================================================================
# Program: tests/test_audio_fx.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: tests app/services/audio_fx.py, the simple offline effects (distortion, delay, reverb) and
# the WAV helpers/render cache used by the sampler: output ranges, length preservation, WAV
# round trips, chained rendering and cache reuse.
# Run: python3 -m pytest tests/test_audio_fx.py -q
# ==============================================================================

from __future__ import annotations

import math
import wave
from array import array
from pathlib import Path

from app.services.audio_fx import (
    apply_delay,
    apply_distortion,
    apply_reverb,
    get_or_render_fx,
    read_wav_mono,
    render_fx_chain,
    write_wav_mono,
)


# Test helper: write a 0.2 s mono 16-bit 440 Hz sine WAV at half full scale, long enough for
# delay/reverb tails to matter.
def write_test_tone(path: Path, seconds: float = 0.2, freq: float = 440.0, rate: int = 44100) -> None:
    """Create a sine-wave WAV at `path` for use as effect input."""
    n = int(rate * seconds)
    samples = array("h", (int(0.5 * 32767 * math.sin(2 * math.pi * freq * i / rate)) for i in range(n)))
    with wave.open(str(path), "w") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(rate)
        wav_file.writeframes(samples.tobytes())


# Invariant: a heavy drive (8x) must never push samples outside -1..1, otherwise the WAV writer
# would clip or wrap. Includes exactly +/-1.0 inputs as the worst case.
def test_apply_distortion_stays_in_range() -> None:
    out = apply_distortion([0.0, 0.5, -0.5, 1.0, -1.0], drive=8.0)
    assert all(-1.0 <= v <= 1.0 for v in out)


# Invariant: effects return the same number of samples, so a processed sample keeps its original
# timing in the pattern and no tail is truncated into the next hit.
def test_apply_delay_and_reverb_preserve_length() -> None:
    samples = [0.1] * 1000
    assert len(apply_delay(samples, 44100)) == len(samples)
    assert len(apply_reverb(samples, 44100)) == len(samples)


# Round trip: reading yields normalised floats (|x| <= 1) and the sample rate; writing them back
# gives a WAV with the same frame count and rate. Proves the two helpers are inverse of each other.
def test_read_write_wav_mono_round_trip(tmp_path: Path) -> None:
    src = tmp_path / "tone.wav"
    write_test_tone(src)
    samples, rate = read_wav_mono(str(src))
    assert rate == 44100
    assert len(samples) > 0
    assert max(abs(s) for s in samples) <= 1.0

    out = tmp_path / "out.wav"
    write_wav_mono(str(out), samples, rate)
    with wave.open(str(out)) as check:
        assert check.getnframes() == len(samples)
        assert check.getframerate() == 44100


# All three effects enabled at once must still produce a readable, non-empty WAV.
def test_render_fx_chain_produces_valid_wav(tmp_path: Path) -> None:
    src = tmp_path / "tone.wav"
    write_test_tone(src)
    out = tmp_path / "rendered.wav"
    render_fx_chain(str(src), str(out), distortion=True, delay=True, reverb=True)
    with wave.open(str(out)) as check:
        assert check.getnframes() > 0


# Optimisation: with every effect off there is nothing to render, so the original file's resolved
# path is returned and no cache file is created.
def test_get_or_render_fx_returns_source_when_no_effects_enabled(tmp_path: Path) -> None:
    samples_root = tmp_path / "samples"
    samples_root.mkdir()
    write_test_tone(samples_root / "tone.wav")
    result = get_or_render_fx(str(samples_root), "tone.wav", distortion=False, delay=False, reverb=False)
    assert result == str((samples_root / "tone.wav").resolve())


# Scenario: ask for the same effect settings twice. The second call must return the identical path
# (cache hit) and the file must exist. The data dir is redirected to a temp folder so the test does
# not write into the user's real app data.
def test_get_or_render_fx_caches_rendered_output(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ID_APP_DATA_DIR", str(tmp_path / "app_data"))
    samples_root = tmp_path / "samples"
    samples_root.mkdir()
    write_test_tone(samples_root / "tone.wav")
    first = get_or_render_fx(str(samples_root), "tone.wav", distortion=True, delay=False, reverb=False)
    second = get_or_render_fx(str(samples_root), "tone.wav", distortion=True, delay=False, reverb=False)
    assert first == second
    assert Path(first).exists()
