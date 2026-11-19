# ==============================================================================
# Program: audio_fx.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: Implements digital signal processing audio effect chains including equalizers, compressors, reverbs, delays, and distortion filters for track and master inserts.
# ==============================================================================
# How this module fits: this is the OFFLINE effects path. Nothing in app/ calls it any more (daw.py comments say its live path replaced the
# get_or_render_fx() version, which runs the same effects in real time through app/services/audio/dsp.py); it is kept, with tests/test_audio_fx.py, as a
# dependency-light reference and is mentioned by app/widgets/fx_rack.py. When used, get_or_render_fx() returns a plain WAV path: each (sample, effects)
# pair is rendered once to the app-data folder and reused. tests/test_audio_fx.py covers the maths.
# Signal model: audio is a list of floats in [-1.0, 1.0], mono, at the file's own sample rate. Fixed chain order: pitch shift -> distortion -> delay -> reverb.
# Inputs: a bundled sample path (validated by security.resolve_sample_path) and boolean flags. Outputs: a cached 16-bit mono WAV.
# Design decisions: pure Python on lists (no numpy) because the project keeps its dependencies small and offline-reviewable; samples are capped at
# MAX_FX_DURATION_SECONDS so a long file cannot stall the UI thread, which is where this runs.
# Known limits (not fixed here): stereo input is averaged to mono; the cache key is only (relative path, flags, semitones), so editing a sample file
# in place or using a different samples_root with the same relative name returns a stale render; the delay and reverb loops are O(n) Python so a
# 6 s clip costs a noticeable fraction of a second; a feedback value of 1.0 or more in apply_delay would grow without bound before the final clipping.

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

# Longest part of a sample that effects are applied to, in seconds. Six seconds covers drum hits and short loops while bounding render time and cache size.
MAX_FX_DURATION_SECONDS = 6.0


def read_wav_mono(path: str, max_duration_seconds: float | None = None) -> tuple[list[float], int]:
    """Load a PCM WAV file as mono floats.

    Args:
        path: file path (already validated by the caller).
        max_duration_seconds: stop reading after this many seconds; None reads the whole file.

    Returns:
        (samples, sample_rate) where samples are floats in about [-1.0, 1.0] and multi-channel audio is averaged to one channel.

    Raises:
        wave.Error / EOFError: if the file is not PCM WAV data (compressed WAVs are not supported by the wave module).
    """
    with wave.open(path, "rb") as wav_file:
        n_channels = wav_file.getnchannels()
        sample_width = wav_file.getsampwidth()
        sample_rate = wav_file.getframerate()
        n_frames = wav_file.getnframes()
        # Truncate before reading so a huge file is never fully loaded into memory.
        if max_duration_seconds is not None:
            n_frames = min(n_frames, int(sample_rate * max_duration_seconds))
        raw = wav_file.readframes(n_frames)

    # A frame holds one sample for every channel; max_val is the magnitude of the most negative integer (2^(bits-1)) so results land in [-1, 1).
    frame_size = sample_width * n_channels
    max_val = float(2 ** (8 * sample_width - 1))
    samples: list[float] = []
    # Walk whole frames only; a trailing partial frame (corrupt length) is ignored by the +1 in the range limit.
    for i in range(0, len(raw) - frame_size + 1, frame_size):
        total = 0
        for ch in range(n_channels):
            offset = i + ch * sample_width
            chunk = raw[offset:offset + sample_width]
            # 8-bit WAV is unsigned (silence = 128), all wider widths are signed little-endian, hence the two branches.
            total += (chunk[0] - 128) if sample_width == 1 else int.from_bytes(chunk, "little", signed=True)
        # Averaging channels keeps loudness comparable to the original and avoids clipping from summing.
        samples.append((total / n_channels) / max_val)
    return samples, sample_rate


def write_wav_mono(path: str, samples: list[float], sample_rate: int) -> None:
    """Write mono floats to a 16-bit PCM WAV, creating the folder if needed.

    Args:
        path: destination file (its folder is created).
        samples: floats; values outside [-1.0, 1.0] are clipped.
        sample_rate: Hz stored in the header.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # Clip first, then scale by 32767 (the largest positive 16-bit value) so full scale never wraps around to a loud click.
    pcm = array("h", (int(max(-1.0, min(1.0, s)) * 32767) for s in samples))
    with wave.open(path, "w") as wav_file:
        # Mono, 2 bytes per sample, matching the "h" (signed 16-bit) array typecode above; array.tobytes uses the machine byte order, which is little-endian on all supported platforms.
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm.tobytes())


def apply_pitch_shift(samples: list[float], semitones: float) -> list[float]:
    """Change pitch by resampling (pitch and length change together, like speeding up a tape).

    Args:
        samples: input floats.
        semitones: +12 raises one octave (half the length); -12 lowers one octave (double length); 0 returns the input unchanged.

    Returns:
        New list with about len(samples) / 2**(semitones/12) values.
    """
    # Vari-speed was chosen over time-stretching because it needs no FFT; the side effect is that duration changes.
    # vari-speed resample: pitch and duration move together, like a turntable/tape
    if not semitones:
        return samples
    # Equal-temperament ratio: each semitone multiplies frequency by 2**(1/12).
    ratio = 2 ** (semitones / 12)
    # Reading faster (ratio > 1) yields fewer output samples; max(1, ...) avoids an empty result for extreme shifts.
    n_out = max(1, int(len(samples) / ratio))
    out = []
    last_idx = len(samples) - 1
    for i in range(n_out):
        # src_pos is a fractional read position in the input; linear interpolation between its two neighbours smooths the result (cheap, slightly dull on high frequencies).
        src_pos = i * ratio
        idx = min(int(src_pos), last_idx)
        frac = src_pos - idx
        # At the last sample there is no neighbour, so reuse it instead of reading past the end.
        next_val = samples[idx + 1] if idx < last_idx else samples[idx]
        out.append(samples[idx] * (1 - frac) + next_val * frac)
    return out


def apply_distortion(samples: list[float], drive: float = 6.0) -> list[float]:
    """Soft-clip with tanh for a warm "overdrive" sound.

    Args:
        samples: input floats.
        drive: pre-gain; 1 is almost clean, 6 (default) is heavily saturated. Output always stays within (-1, 1).
    """
    # tanh squashes large values smoothly toward +/-1 instead of hard clipping, which sounds less harsh.
    return [math.tanh(s * drive) for s in samples]


def apply_delay(samples: list[float], sample_rate: int, delay_ms: float = 220.0,
                 feedback: float = 0.35, mix: float = 0.35) -> list[float]:
    """Echo effect with feedback.

    Args:
        samples: input floats.
        sample_rate: Hz, used to convert milliseconds to samples.
        delay_ms: time between repeats (default 220 ms).
        feedback: fraction of each echo fed back in (0 = one echo, near 1 = very long tail; keep below 1).
        mix: wet/dry balance (0 = dry only, 1 = echoes only).

    Returns:
        New list the same length as the input (the tail is cut at the end of the clip).
    """
    delay_n = max(1, int(sample_rate * delay_ms / 1000))
    # A circular buffer the length of the delay: reading and writing the same slot gives exactly delay_n samples of latency.
    buffer = [0.0] * delay_n
    out = list(samples)
    idx = 0
    for i, s in enumerate(out):
        # delayed is the value written delay_n samples ago; storing input plus scaled echo back creates the repeating feedback.
        delayed = buffer[idx]
        buffer[idx] = s + delayed * feedback
        # Dry/wet crossfade; (1 - mix) keeps the total level roughly constant.
        out[i] = s * (1 - mix) + delayed * mix
        idx = (idx + 1) % delay_n
    return out


def apply_reverb(samples: list[float], sample_rate: int, decay: float = 0.5, mix: float = 0.3) -> list[float]:
    """Simple Schroeder-style reverb built from four parallel feedback comb filters.

    Args:
        samples: input floats.
        sample_rate: Hz.
        decay: feedback of each comb (0.5 default; closer to 1 means a longer tail but risks ringing).
        mix: wet/dry balance, 0 to 1.

    Returns:
        New list the same length as the input.
    """
    # Parallel combs with unrelated delays (29.7-43.7 ms, all non-multiples of each other) avoid a single metallic resonance; there is no all-pass diffusion stage, so the tail is simpler than a real reverb.
    # Schroeder-style: a few parallel comb filters, summed and blended with the dry signal
    comb_delays_ms = (29.7, 37.1, 41.3, 43.7)
    # wet accumulates the output of every comb; it is averaged below so adding combs does not raise the level.
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
    # Dividing by the comb count keeps the wet level independent of how many combs there are.
    n_combs = len(comb_delays_ms)
    return [d * (1 - mix) + (w / n_combs) * mix for d, w in zip(samples, wet)]


def render_fx_chain(source_path: str, output_path: str, *, distortion: bool, delay: bool, reverb: bool,
                     semitones: float = 0) -> None:
    """Run the full effect chain and write the result.

    Args:
        source_path: validated input WAV.
        output_path: destination WAV.
        distortion, delay, reverb: switch each effect on or off (keyword-only to prevent mixing them up).
        semitones: pitch shift; 0 skips it.

    Chain order is fixed: pitch, distortion, delay, reverb. Distortion before the time effects means the echoes of a distorted note stay clean-sounding copies of it.
    """
    # Only the first MAX_FX_DURATION_SECONDS are processed, so the output may be shorter than the source.
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
    """Return (and create if needed) the private folder that holds rendered effect files.

    Returns:
        Absolute folder path under the app-data directory.
    """
    fx_dir = os.path.join(get_app_data_dir(), "fx_render")
    os.makedirs(fx_dir, exist_ok=True)
    # Owner-only permissions so other local users cannot read or replace rendered audio; failure (Windows, odd filesystems) is ignorable.
    try:
        os.chmod(fx_dir, 0o700)
    except OSError:
        pass
    return fx_dir


def get_or_render_fx(samples_root: str, relative_sample_path: str, *,
                      distortion: bool, delay: bool, reverb: bool, semitones: float = 0) -> str:
    """Return a playable file for a sample with the requested effects, rendering it only if needed.

    Args:
        samples_root: folder of bundled samples.
        relative_sample_path: sample name inside that folder (validated, so traversal like "../" raises SecurityError).
        distortion, delay, reverb: effect switches.
        semitones: pitch shift in semitones.

    Returns:
        The original sample path when no effect is on, otherwise the cached render path.

    Raises:
        SecurityError: invalid sample name.
    """
    source_path = resolve_sample_path(samples_root, relative_sample_path)
    # No effects: skip rendering entirely and play the original.
    if not (distortion or delay or reverb or semitones):
        return source_path

    # Cache key encodes everything that changes the sound. SHA-1 is used only to make a short, filename-safe name, not for security; 16 hex characters make an accidental clash very unlikely.
    key = f"{relative_sample_path}|{int(distortion)}{int(delay)}{int(reverb)}|{semitones}"
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
    output_path = os.path.join(get_fx_cache_dir(), f"{digest}.wav")
    # Reuse an earlier render. There is no invalidation (see known limits in the file header).
    if not os.path.exists(output_path):
        render_fx_chain(source_path, output_path, distortion=distortion, delay=delay, reverb=reverb,
                         semitones=semitones)
    return output_path
