from __future__ import annotations

import math
import os
import wave
from array import array
from dataclasses import dataclass

from app.security import MAX_OUTPUT_FILE_BYTES, SecurityError, resolve_user_output_file
from app.services.audio_fx import get_or_render_fx, read_wav_mono


EXPORT_SAMPLE_RATE = 44100
MAX_EXPORT_SECONDS = 60
MAX_EXPORT_TRACKS = 16
MAX_ACTIVE_STEPS = 16


@dataclass(frozen=True)
class ExportTrack:
    relative_path: str | None
    steps: list[bool]
    distortion: bool = False
    delay: bool = False
    reverb: bool = False
    semitones: float = 0


def resample_linear(samples: list[float], source_rate: int, target_rate: int) -> list[float]:
    if source_rate == target_rate or not samples:
        return samples
    ratio = source_rate / target_rate
    out_len = max(1, int(len(samples) / ratio))
    out: list[float] = []
    last_idx = len(samples) - 1
    for i in range(out_len):
        src_pos = i * ratio
        idx = min(int(src_pos), last_idx)
        frac = src_pos - idx
        next_val = samples[idx + 1] if idx < last_idx else samples[idx]
        out.append(samples[idx] * (1 - frac) + next_val * frac)
    return out


def write_pcm16_wav(path: str, samples: list[float], sample_rate: int = EXPORT_SAMPLE_RATE) -> None:
    estimated_size = len(samples) * 2 + 44
    if estimated_size > MAX_OUTPUT_FILE_BYTES:
        raise SecurityError("Export is too large.")
    pcm = array("h", (int(max(-1.0, min(1.0, sample)) * 32767) for sample in samples))
    with wave.open(path, "w") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm.tobytes())


def export_pattern_wav(output_path: str, samples_root: str, bpm: int, tracks: list[ExportTrack]) -> str:
    if not 60 <= int(bpm) <= 200:
        raise SecurityError("BPM is outside the safe export range.")
    if len(tracks) > MAX_EXPORT_TRACKS:
        raise SecurityError("Too many tracks to export safely.")

    output = resolve_user_output_file(output_path, ".wav")
    step_samples = int((60.0 / int(bpm)) / 4.0 * EXPORT_SAMPLE_RATE)
    total_samples = min(16 * step_samples + EXPORT_SAMPLE_RATE * 4, EXPORT_SAMPLE_RATE * MAX_EXPORT_SECONDS)
    master = [0.0] * total_samples

    for track in tracks:
        if not track.relative_path:
            continue
        active_steps = [idx for idx, active in enumerate(track.steps[:16]) if active]
        if len(active_steps) > MAX_ACTIVE_STEPS:
            raise SecurityError("Too many active steps.")
        if not active_steps:
            continue
        audio_path = get_or_render_fx(
            samples_root,
            track.relative_path,
            distortion=track.distortion,
            delay=track.delay,
            reverb=track.reverb,
            semitones=track.semitones,
        )
        if not os.path.isfile(audio_path):
            continue
        samples, sample_rate = read_wav_mono(audio_path, max_duration_seconds=6.0)
        samples = resample_linear(samples, sample_rate, EXPORT_SAMPLE_RATE)

        for step_idx in active_steps:
            start = step_idx * step_samples
            if start >= total_samples:
                continue
            copy_len = min(len(samples), total_samples - start)
            for offset in range(copy_len):
                master[start + offset] += samples[offset]

    clipped = [math.tanh(sample) for sample in master]
    write_pcm16_wav(output, clipped, EXPORT_SAMPLE_RATE)
    return output
