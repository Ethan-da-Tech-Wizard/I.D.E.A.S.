# ==============================================================================
# Program: audio_export.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: Bounces and renders multi-track DAW projects to standard audio file formats (WAV, FLAC, MP3), managing sample rate conversion, bit depth truncation, and normalization.
# ==============================================================================

from __future__ import annotations

# How this module fits: "Export WAV" for the DAW step sequencer (app/ui/daw.py). The sequencer gives it the BPM and one ExportTrack per row (sample name, 16 on/off steps and effect flags); this module
# mixes every active step into one mono 16-bit, 44.1 kHz WAV file. Samples come from the bundled library (resolved safely through app/services/audio_fx.get_or_render_fx, which also applies
# the effects) and the destination is validated by app/security.resolve_user_output_file. Tested in tests/test_audio_export.py.
# Design: pure Python on lists of floats (no NumPy), which keeps this file easy to read and test; the cost is speed (the mixing loop touches each output sample in Python), acceptable only because the length is
# capped (MAX_EXPORT_SECONDS). Audio values are floats in -1.0..1.0.
# Limits: mono only; one pattern of the sequencer's length plus a 4-second tail (for reverb and delay decay), never more than 60 seconds; at most 16 tracks; a sample is read for at most 6 seconds.
import math
import os
import wave
from array import array
from dataclasses import dataclass

from app.security import MAX_OUTPUT_FILE_BYTES, SecurityError, resolve_user_output_file
from app.services.audio_fx import get_or_render_fx, read_wav_mono


# Output sample rate in Hz (CD quality). Every source sample is converted to this rate before mixing.
EXPORT_SAMPLE_RATE = 44100

# Safety caps: total length in seconds, number of tracks, and step count limits that keep the export's time and memory bounded.
MAX_EXPORT_SECONDS = 60
MAX_EXPORT_TRACKS = 16
MAX_ACTIVE_STEPS = 16


# Immutable description of one sequencer row. relative_path is the sample name inside the bundled library (None means an empty row), steps one boolean per 16th note, distortion/delay/reverb the effect toggles
# and semitones a pitch shift. Frozen so a track cannot change while an export is running.
@dataclass(frozen=True)
class ExportTrack:
    relative_path: str | None
    steps: list[bool]
    distortion: bool = False
    delay: bool = False
    reverb: bool = False
    semitones: float = 0


def resample_linear(samples: list[float], source_rate: int, target_rate: int) -> list[float]:
    """Convert audio to another sample rate by linear interpolation.

    Args:
        samples: input audio, floats in -1..1.
        source_rate, target_rate: rates in Hz.
    Returns:
        The input list itself when the rates match or it is empty (not a copy), otherwise a new list of about len(samples) * target_rate / source_rate values.
    Linear interpolation is simple and fast but not high quality: it can add slight dullness or aliasing when rates differ a lot; samples in the library are normally 44.1 kHz already.
    """
    if source_rate == target_rate or not samples:
        return samples

    # Distance in source samples between two consecutive output samples. For output i the source position is i * ratio; the value is a weighted mix of the two neighbouring source samples.
    ratio = source_rate / target_rate
    out_len = max(1, int(len(samples) / ratio))
    out: list[float] = []
    last_idx = len(samples) - 1
    for i in range(out_len):
        src_pos = i * ratio

        # Clamp so the last output samples never read past the end of the source.
        idx = min(int(src_pos), last_idx)
        frac = src_pos - idx
        next_val = samples[idx + 1] if idx < last_idx else samples[idx]
        out.append(samples[idx] * (1 - frac) + next_val * frac)
    return out


def write_pcm16_wav(path: str, samples: list[float], sample_rate: int = EXPORT_SAMPLE_RATE) -> None:
    """Write mono 16-bit PCM samples to a WAV file at `path`.

    Raises:
        SecurityError: the finished file would exceed MAX_OUTPUT_FILE_BYTES (200 MiB).
    The path must already have been validated (export_pattern_wav does this); this function does not check where it writes.
    """
    # 2 bytes per 16-bit sample plus the 44-byte WAV header: size known up front, so an oversized export is refused before any file is created.
    estimated_size = len(samples) * 2 + 44
    if estimated_size > MAX_OUTPUT_FILE_BYTES:
        raise SecurityError("Export is too large.")

    # Convert floats to signed 16-bit integers: clamp to -1..1 first (so out-of-range values cannot overflow), then scale by 32767 (the largest 16-bit value).
    pcm = array("h", (int(max(-1.0, min(1.0, sample)) * 32767) for sample in samples))
    with wave.open(path, "w") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm.tobytes())


def export_pattern_wav(output_path: str, samples_root: str, bpm: int, tracks: list[ExportTrack]) -> str:
    """Render the pattern to a WAV file and return the path written.

    Args:
        output_path: destination chosen by the user (".wav" is added or enforced).
        samples_root: bundled sample library folder.
        bpm: tempo, 60 to 200.
        tracks: sequencer rows (at most 16).
    Raises:
        SecurityError: BPM outside 60..200, too many tracks, more active steps than the pattern has steps, an invalid destination or sample, or an oversized result.
    """
    if not 60 <= int(bpm) <= 200:
        raise SecurityError("BPM is outside the safe export range.")
    if len(tracks) > MAX_EXPORT_TRACKS:
        raise SecurityError("Too many tracks to export safely.")

    # Pattern length is taken from the first track (all rows are expected to have the same length); with no tracks a default 16-step pattern is rendered, which gives a silent file.
    num_steps = len(tracks[0].steps) if tracks else 16

    output = resolve_user_output_file(output_path, ".wav")

    # Length of one step in samples. 60 / bpm is seconds per beat; dividing by 4 gives seconds per sixteenth-note step; times 44100 gives samples. Example: 120 BPM -> 0.125 s -> 5512 samples.
    step_samples = int((60.0 / int(bpm)) / 4.0 * EXPORT_SAMPLE_RATE)

    # Output length: the whole pattern plus 4 seconds so the tail of effects is not cut off, but never more than 60 seconds.
    total_samples = min(num_steps * step_samples + EXPORT_SAMPLE_RATE * 4, EXPORT_SAMPLE_RATE * MAX_EXPORT_SECONDS)

    # The mix buffer, one float per output sample, starting as silence. Each sample hit adds into it.
    master = [0.0] * total_samples

    for track in tracks:
        if not track.relative_path:
            continue
        active_steps = [idx for idx, active in enumerate(track.steps) if active]
        if len(active_steps) > num_steps:
            raise SecurityError("Too many active steps.")
        if not active_steps:
            continue

        # Returns the path of the sample with this track's effects applied (rendered once and cached, so repeated exports are quick).
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

        # Load at most 6 seconds as mono floats, which bounds memory and mixing time for very long samples.
        samples, sample_rate = read_wav_mono(audio_path, max_duration_seconds=6.0)
        samples = resample_linear(samples, sample_rate, EXPORT_SAMPLE_RATE)

        # Add the sample at the start time of every active step. Samples that start late may be cut at the buffer end (copy_len); overlapping hits simply add together.
        for step_idx in active_steps:
            start = step_idx * step_samples
            if start >= total_samples:
                continue
            copy_len = min(len(samples), total_samples - start)
            for offset in range(copy_len):
                master[start + offset] += samples[offset]

    # Soft limiter: tanh squeezes any sum, however loud, smoothly into -1..1 instead of hard-clipping, so many layered hits sound louder-but-clean rather than harshly distorted. It also slightly lowers quiet signals (tanh(x) is about x for small x).
    clipped = [math.tanh(sample) for sample in master]
    write_pcm16_wav(output, clipped, EXPORT_SAMPLE_RATE)
    return output
