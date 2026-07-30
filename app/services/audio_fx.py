"""Offline sample effects: Distortion -> Delay -> Reverb, rendered to a cached WAV.

QSoundEffect/QMediaPlayer have no live DSP graph, so effects are pre-rendered
to disk once per (sample, effect-combo) and cached, rather than processed in
real time.
"""
from __future__ import annotations

import hashlib
import math
import os
import wave
from array import array

from app.security import resolve_sample_path
from app.storage.database import get_app_data_dir

MAX_FX_DURATION_SECONDS = 6.0


def read_wav_mono(path: str, max_duration_seconds: float | None = None) -> tuple[list[float], int]:
    with wave.open(path, "rb") as wav_file:
        n_channels = wav_file.getnchannels()
        sample_width = wav_file.getsampwidth()
        sample_rate = wav_file.getframerate()
        n_frames = wav_file.getnframes()
        if max_duration_seconds is not None:
            n_frames = min(n_frames, int(sample_rate * max_duration_seconds))
        raw = wav_file.readframes(n_frames)

    frame_size = sample_width * n_channels
    max_val = float(2 ** (8 * sample_width - 1))
    samples: list[float] = []
    for i in range(0, len(raw) - frame_size + 1, frame_size):
        total = 0
        for ch in range(n_channels):
            offset = i + ch * sample_width
            chunk = raw[offset:offset + sample_width]
            total += (chunk[0] - 128) if sample_width == 1 else int.from_bytes(chunk, "little", signed=True)
        samples.append((total / n_channels) / max_val)
    return samples, sample_rate


def write_wav_mono(path: str, samples: list[float], sample_rate: int) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    pcm = array("h", (int(max(-1.0, min(1.0, s)) * 32767) for s in samples))
    with wave.open(path, "w") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm.tobytes())


def apply_pitch_shift(samples: list[float], semitones: float) -> list[float]:
    # vari-speed resample: pitch and duration move together, like a turntable/tape
    if not semitones:
        return samples
    ratio = 2 ** (semitones / 12)
    n_out = max(1, int(len(samples) / ratio))
    out = []
    last_idx = len(samples) - 1
    for i in range(n_out):
        src_pos = i * ratio
        idx = min(int(src_pos), last_idx)
        frac = src_pos - idx
        next_val = samples[idx + 1] if idx < last_idx else samples[idx]
        out.append(samples[idx] * (1 - frac) + next_val * frac)
    return out


def apply_distortion(samples: list[float], drive: float = 6.0) -> list[float]:
    return [math.tanh(s * drive) for s in samples]


def apply_delay(samples: list[float], sample_rate: int, delay_ms: float = 220.0,
                 feedback: float = 0.35, mix: float = 0.35) -> list[float]:
    delay_n = max(1, int(sample_rate * delay_ms / 1000))
    buffer = [0.0] * delay_n
    out = list(samples)
    idx = 0
    for i, s in enumerate(out):
        delayed = buffer[idx]
        buffer[idx] = s + delayed * feedback
        out[i] = s * (1 - mix) + delayed * mix
        idx = (idx + 1) % delay_n
    return out


def apply_reverb(samples: list[float], sample_rate: int, decay: float = 0.5, mix: float = 0.3) -> list[float]:
    # Schroeder-style: a few parallel comb filters, summed and blended with the dry signal
    comb_delays_ms = (29.7, 37.1, 41.3, 43.7)
    wet = [0.0] * len(samples)
    for delay_ms in comb_delays_ms:
        delay_n = max(1, int(sample_rate * delay_ms / 1000))
        buffer = [0.0] * delay_n
        idx = 0
        for i, s in enumerate(samples):
            delayed = buffer[idx]
            buffer[idx] = s + delayed * decay
            wet[i] += delayed
            idx = (idx + 1) % delay_n
    n_combs = len(comb_delays_ms)
    return [d * (1 - mix) + (w / n_combs) * mix for d, w in zip(samples, wet)]


def render_fx_chain(source_path: str, output_path: str, *, distortion: bool, delay: bool, reverb: bool,
                     semitones: float = 0) -> None:
    samples, sample_rate = read_wav_mono(source_path, MAX_FX_DURATION_SECONDS)
    if semitones:
        samples = apply_pitch_shift(samples, semitones)
    if distortion:
        samples = apply_distortion(samples)
    if delay:
        samples = apply_delay(samples, sample_rate)
    if reverb:
        samples = apply_reverb(samples, sample_rate)
    write_wav_mono(output_path, samples, sample_rate)


def get_fx_cache_dir() -> str:
    fx_dir = os.path.join(get_app_data_dir(), "fx_render")
    os.makedirs(fx_dir, exist_ok=True)
    try:
        os.chmod(fx_dir, 0o700)
    except OSError:
        pass
    return fx_dir


def get_or_render_fx(samples_root: str, relative_sample_path: str, *,
                      distortion: bool, delay: bool, reverb: bool, semitones: float = 0) -> str:
    source_path = resolve_sample_path(samples_root, relative_sample_path)
    if not (distortion or delay or reverb or semitones):
        return source_path

    key = f"{relative_sample_path}|{int(distortion)}{int(delay)}{int(reverb)}|{semitones}"
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
    output_path = os.path.join(get_fx_cache_dir(), f"{digest}.wav")
    if not os.path.exists(output_path):
        render_fx_chain(source_path, output_path, distortion=distortion, delay=delay, reverb=reverb,
                         semitones=semitones)
    return output_path
