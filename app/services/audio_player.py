# ==============================================================================
# Program: audio_player.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: Controls low-latency audio stream playback, buffer scheduling, transport controls (play, pause, seek, loop), and audio hardware output device routing.
# ==============================================================================
# How this module fits: app/ui/daw.py creates one SamplePlayer per track and calls set_source/play/stop from the sequencer timer when a step fires.
# It plays by launching the operating system's command-line player as a child process, so the app needs no audio library for this path
# (the other path, app/services/audio/engine.py, does real-time mixing instead).
# Inputs: a WAV path. Output: sound via paplay (PulseAudio/PipeWire), aplay (ALSA) or afplay (macOS), chosen once at import time.
# Design decisions: a new short process per trigger is crude but avoids the dropouts seen with Qt multimedia when retriggering fast;
# the child is started with a list of arguments (no shell) so a file name can never be interpreted as a command.
# Known limits: process start-up adds latency (tens of milliseconds), so tight timing is not possible; play() kills the previous process of the
# same player, so two hits on ONE track never overlap even though the class text says the audio server mixes overlapping calls (it
# only mixes different players); there is no volume, pan or seek; if no player program is installed every call is silently ignored.
# Testing: no dedicated test; run the app with a track and check that sound is produced.

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
    """Pick the first system audio player that exists.

    Returns:
        Argument list to start the player (the file path is appended later) or None when nothing is installed.
    """
    # Order of preference: PulseAudio/PipeWire first (works with modern Linux desktops), then ALSA directly, then macOS afplay.
    if shutil.which("paplay"):
        return ["paplay"]
    if shutil.which("aplay"):
        # "-q" silences aplay's status line, which would otherwise be discarded anyway.
        return ["aplay", "-q"]
    if shutil.which("afplay"):  # macOS
        return ["afplay"]
    return None


# Resolved once at import; installing a player while the app runs requires a restart.
_PLAY_CMD = _find_play_command()


class SamplePlayer:
    """Plays one WAV sample; the DAW keeps one instance per track.

    Each call to play() starts a short-lived child process that writes audio to PulseAudio/ALSA and exits.
    Calling play() again on the same instance first kills the earlier process (see the module header for why).
    """

    def __init__(self) -> None:
        """Start with no sample loaded and no running process."""
        self._source_path: Optional[str] = None
        self._proc: Optional[subprocess.Popen] = None

    def set_source(self, path: str) -> None:
        """Choose the WAV file to play.

        Args:
            path: file path; a missing file or empty string clears the source so play() does nothing instead of failing.
        """
        if path and os.path.isfile(path):
            self._source_path = path
        else:
            self._source_path = None

    def play(self) -> None:
        """Start playing the loaded sample without waiting for it to finish.

        Does nothing if no source is set or no system player exists. Errors starting the process are swallowed so the sequencer never stops because of audio trouble.
        """
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
        # Output is discarded: the player tools print progress or warnings that would otherwise end up in the terminal.
        try:
            self._proc = subprocess.Popen(
                _PLAY_CMD + [self._source_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        # Player vanished or fork failed: treat as silence.
        except OSError:
            self._proc = None

    def stop(self) -> None:
        """Stop this track's current playback, if any. Safe to call when nothing is playing."""
        if self._proc is not None:
            try:
                self._proc.kill()
            except OSError:
                pass
            self._proc = None

    # Static method: availability is a property of the machine, not of one player instance, so the UI can ask before creating players.
    @staticmethod
    def is_available() -> bool:
        """Report whether a system audio player was found.

        Returns:
            True when play() can produce sound.
        """
        return _PLAY_CMD is not None
