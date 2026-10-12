"""Lightweight audio playback engine using PulseAudio (paplay) with fallback.

QSoundEffect and QMediaPlayer both fail to produce reliable audio on
PipeWire + FFmpeg Qt backends when rapidly retriggering short samples.
This module uses the system's native `paplay` command instead, which
works directly with PipeWire/PulseAudio without buffering issues.

On systems without paplay, it falls back to `aplay` (ALSA).
"""
from __future__ import annotations

import os
import shutil
import subprocess
from typing import Optional


def _find_play_command() -> Optional[list[str]]:
    """Find the best available command-line audio player."""
    if shutil.which("paplay"):
        return ["paplay"]
    if shutil.which("aplay"):
        return ["aplay", "-q"]
    if shutil.which("afplay"):  # macOS
        return ["afplay"]
    return None


_PLAY_CMD = _find_play_command()


class SamplePlayer:
    """Plays a single WAV sample. One instance per DAW track.

    Each call to play() spawns a short-lived background process that
    writes audio to PulseAudio/ALSA and exits. Overlapping calls are
    fine — the audio server mixes them automatically.
    """

    def __init__(self) -> None:
        self._source_path: Optional[str] = None
        self._proc: Optional[subprocess.Popen] = None

    def set_source(self, path: str) -> None:
        """Set the WAV file to play."""
        if path and os.path.isfile(path):
            self._source_path = path
        else:
            self._source_path = None

    def play(self) -> None:
        """Fire-and-forget: play the loaded sample asynchronously."""
        if not self._source_path or _PLAY_CMD is None:
            return
        # Kill any previous still-playing instance of this track
        # (prevents pile-up on very fast BPMs)
        if self._proc is not None:
            try:
                self._proc.kill()
            except OSError:
                pass
            self._proc = None
        try:
            self._proc = subprocess.Popen(
                _PLAY_CMD + [self._source_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            self._proc = None

    def stop(self) -> None:
        """Stop current playback if any."""
        if self._proc is not None:
            try:
                self._proc.kill()
            except OSError:
                pass
            self._proc = None

    @staticmethod
    def is_available() -> bool:
        """Return True if a system audio player was found."""
        return _PLAY_CMD is not None
