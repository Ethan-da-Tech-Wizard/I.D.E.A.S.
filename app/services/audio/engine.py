# ==============================================================================
# Program: engine.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: Master audio engine core coordinating real-time audio playback threads, mixer graph evaluation, voice summing, and ASIO/CoreAudio device interaction.
# ==============================================================================
# How this module fits: app/ui/daw.py (through a thin Qt adapter) creates one AudioEngine, adds a TrackProcessor per DAW track, sets the tempo
# and calls start()/stop(). The engine opens one sounddevice output stream; the sound card repeatedly calls _callback() on its own high-priority
# thread asking for the next block of audio. In each call the engine (1) advances the sample-accurate step clock and fires on_step_boundary,
# (2) asks every un-muted track for its block and sums them, (3) applies the master fader and the Limiter, (4) writes the result to the device,
# (5) fires scheduled note-offs, (6) measures how long all that took and sheds load if the CPU is falling behind.
# Inputs: track objects (app/services/audio/track_engine.py), step triggers from the UI, tempo. Outputs: sound, plus three optional callbacks
# (on_step_boundary(step_index), on_meter_update(dict), on_overload_changed(bool)) that run on the AUDIO thread.
# Design decisions: time is counted in samples, not seconds, so the step clock never drifts against the audio; tempo changes wait for the next
# step boundary so a step is never cut short; overload shedding bypasses compressors first, then reverbs, quietest tracks first, and recovers
# one level after a second of headroom; the module imports no Qt so it can be tested with plain pytest (tests/test_audio_engine.py).
# Known limits noticed (comment pass only):
#   - The master scratch buffer holds max_block_size (4096) frames; a device that delivers larger blocks would raise inside the callback.
#   - After a tempo change, on_step_boundary reports next_boundary // step_frames using the NEW step length, so the index can jump or repeat
#     relative to the old tempo; the UI should treat it as a pulse more than as an absolute position after a tempo change.
#   - Callbacks run on the audio thread; if one raises, the exception propagates into the sounddevice callback and the stream may stop.
#   - The overload check measures only this callback's compute time, not driver underruns reported through `status`.
#   - sounddevice (PortAudio) is a runtime dependency but is imported only in start().

"""Real-time audio engine: owns the sounddevice callback stream, the
sample-accurate step clock, master bus summing + limiter, and overload
shedding, per docs/DAW_VISION_SPEC.md's "Architecture Decision: Live Audio
Backend" and "Real-Time Safety Contract" sections.

Deliberately has zero PySide6/Qt imports so it can be unit-tested with
plain pytest (ARCHITECTURE.md requires non-GUI business logic stay
QApplication-free). UI integration is a thin adapter elsewhere that
subscribes to on_step_boundary/on_meter_update/on_overload_changed and
re-emits them as Qt signals via Qt.ConnectionType.QueuedConnection -- never
call Qt widget methods directly from these hooks, they fire on the audio
callback thread.
"""
from __future__ import annotations

import time
from typing import Callable

import numpy as np

from app.services.audio.dsp import Limiter, SAMPLE_RATE, db_to_linear
from app.services.audio.track_engine import TrackProcessor
from app.services.audio.voice import Voice

# Tuning constants. DEFAULT_BLOCK_SIZE: 1024 frames = about 21 ms at 48 kHz, a compromise between latency and CPU. GLOBAL_VOICE_CAP: at most 256 notes
# sounding in total, so a runaway sequence cannot exhaust the CPU. OVERLOAD_THRESHOLD_FRACTION: a block counts as "late" when processing took more than
# 70% of the time the block lasts (leaving margin before an actual dropout). OVERLOAD_CONSECUTIVE_BLOCKS: two late blocks in a row before shedding,
# to ignore one-off spikes. METER_EMIT_HZ: UI meter updates per second. MASTER_FADER_MAX_DB: fader cannot boost more than +6 dB.
DEFAULT_BLOCK_SIZE = 1024
GLOBAL_VOICE_CAP = 256
OVERLOAD_THRESHOLD_FRACTION = 0.70
OVERLOAD_CONSECUTIVE_BLOCKS = 2
METER_EMIT_HZ = 30
MASTER_FADER_MAX_DB = 6.0


class ChokeGroupRegistry:
    """Cross-track choke groups: triggering a note on one track silences the sounding notes of the other tracks in the same numbered group
    (for example an open hi-hat is cut by a closed hi-hat). See "Voice & Polyphony Rules" in docs/DAW_VISION_SPEC.md.
    Kept outside TrackProcessor because a track has no visibility into its siblings.
    """

    def __init__(self) -> None:
        """Start with no groups. Maps group number -> list of member tracks."""
        self._groups: dict[int, list[TrackProcessor]] = {}

    def register(self, track: TrackProcessor) -> None:
        """Add a track to its choke group; tracks without a group are ignored."""
        if track.choke_group is not None:
            self._groups.setdefault(track.choke_group, []).append(track)

    def unregister(self, track: TrackProcessor) -> None:
        """Remove a track from its group (called when the track is deleted)."""
        if track.choke_group is not None:
            group = self._groups.get(track.choke_group)
            if group and track in group:
                group.remove(track)

    def notify_trigger(self, track: TrackProcessor) -> None:
        """Silence every other member of this track's group.

        Args:
            track: the track that was just triggered; it keeps playing.
        """
        if track.choke_group is None:
            return
        for other in self._groups.get(track.choke_group, ()):
            if other is not track:
                other.choke_all_voices()


class AudioEngine:
    """Owns the output stream, the master bus, the transport clock and the overload protection.

    Threading: UI-facing methods (add_track, trigger_step_note, set_bpm, start, stop) are called from the GUI thread while _callback runs on the audio
    thread. Shared fields are simple values or lists replaced wholesale and there is no lock by design (locks can block the audio thread),
    so the engine relies on those assignments being atomic in CPython.
    """
    def __init__(self, sample_rate: int = SAMPLE_RATE, block_size: int = DEFAULT_BLOCK_SIZE,
                 max_block_size: int = 4096) -> None:
        """Prepare the engine without opening an audio device.

        Args:
            sample_rate: Hz (default 48000).
            block_size: frames per callback (default 1024).
            max_block_size: size of preallocated scratch buffers; must be at least block_size.
        """
        self.sample_rate = sample_rate
        self.block_size = block_size
        # Default tempo 120 BPM; master fader at 0 dB (unity).
        self.bpm = 120.0
        self.master_fader_db = 0.0

        # Preallocated master mix buffer (frames x 2 channels) so the callback does not allocate.
        self._max_block_size = max_block_size
        self._tracks: dict[str, TrackProcessor] = {}
        self._choke_registry = ChokeGroupRegistry()
        self._master_scratch = np.zeros((max_block_size, 2), dtype=np.float32)
        self._limiter = Limiter(sample_rate)

        # _sample_position counts frames since start(); everything (step clock, note-off scheduling) is measured against it.
        # _pending_bpm holds a tempo change until the next step boundary; _pending_note_offs lists (due frame, voice) pairs for gate-length note-offs.
        self._sample_position = 0
        self._is_playing = False
        self._pending_bpm: float | None = None
        self._pending_note_offs: list[tuple[int, Voice]] = []

        # Overload state: how many late blocks in a row, whether shedding is active, spare time banked since the last late block, and how many
        # load-shedding levels are currently applied (see _shed_targets).
        self._overload_consecutive = 0
        self._overload_active = False
        self._headroom_samples = 0
        self._shed_count = 0

        # Meter updates are rate-limited by counting frames: 48000 / 30 = 1600 frames between UI updates.
        self._meter_emit_interval_frames = max(1, int(sample_rate / METER_EMIT_HZ))
        self._meter_frame_counter = 0

        # The stream object only exists between start() and stop().
        self._stream = None  # sounddevice.OutputStream, created in start()

        # Plain-callable hooks -- see module docstring re: Qt.
        # Plain-callable hooks -- see module docstring re: Qt.
        self.on_step_boundary: Callable[[int], None] | None = None
        self.on_meter_update: Callable[[dict], None] | None = None
        self.on_overload_changed: Callable[[bool], None] | None = None

    # -- track management ---------------------------------------------

    def add_track(self, track: TrackProcessor) -> None:
        """Register a track with the engine (and its choke group).

        Args:
            track: a TrackProcessor with a unique track_id; adding the same id again replaces the old one in the dictionary but not in the choke registry.
        """
        self._tracks[track.track_id] = track
        self._choke_registry.register(track)

    def remove_track(self, track_id: str) -> None:
        """Remove a track by id (no error if it does not exist)."""
        track = self._tracks.pop(track_id, None)
        if track is not None:
            self._choke_registry.unregister(track)

    def _total_voice_count(self) -> int:
        """Total number of sounding voices across all tracks, used to enforce GLOBAL_VOICE_CAP."""
        return sum(t.voice_count for t in self._tracks.values())

    # -- triggering ------------------------------------------------------

    # Tempo maths: one beat lasts 60/bpm seconds and a step is a sixteenth note, i.e. a quarter of a beat.
    def _current_step_frames(self) -> int:
        """16th-note step length in samples, matching the existing
        AudioEngineThread's `(60.0 / bpm) / 4.0` interval convention in
        app/ui/daw.py."""
        return max(1, int(self.sample_rate * (60.0 / self.bpm) / 4.0))

    def trigger_step_note(self, track_id: str, gate_length_pct: float) -> Voice | None:
        """Fire a sequencer step note and schedule its note-off.

        Args:
            track_id: which track to play.
            gate_length_pct: how long the note is held as a percentage of one step (100 = until the next step).

        Returns:
            The new Voice, or None when the voice cap is reached or the track id is unknown (the step is silently skipped).
        """
        # Over the cap: drop the note rather than risk overloading the CPU.
        if self._total_voice_count() >= GLOBAL_VOICE_CAP:
            return None
        track = self._tracks.get(track_id)
        if track is None:
            return None
        # Trigger first, then choke the other tracks in the group, so the new note is never cut by its own group.
        voice = track.trigger_note()
        self._choke_registry.notify_trigger(track)
        # Convert the gate percentage to frames; at least 1 so a tiny gate still produces a note-off.
        gate_frames = max(1, int(self._current_step_frames() * (gate_length_pct / 100.0)))
        # Absolute due time = now + gate; _apply_pending_note_offs releases it later in the audio callback.
        self._pending_note_offs.append((self._sample_position + gate_frames, voice))
        return voice

    def note_on(self, track_id: str) -> Voice | None:
        """Start a note that lasts until released (live MIDI key down).

        Args:
            track_id: which track to play.

        Returns:
            The Voice to hand back to note_off_voice() on key-up, or None (cap reached or unknown track).
        """
        if self._total_voice_count() >= GLOBAL_VOICE_CAP:
            return None
        track = self._tracks.get(track_id)
        if track is None:
            return None
        voice = track.trigger_note()
        self._choke_registry.notify_trigger(track)
        return voice

    def note_off_voice(self, voice: Voice) -> None:
        """Release a held note (MIDI key up); the envelope's release stage then fades it out."""
        voice.note_off()

    # -- transport ---------------------------------------------------

    def set_bpm(self, bpm: float) -> None:
        """Request a tempo change.

        Args:
            bpm: beats per minute; values below 20 are raised to 20 (the minimum supported tempo).

        The value is stored and applied by _apply_step_clock at the next step boundary.
        """
        self._pending_bpm = max(20.0, bpm)

    def is_playing(self) -> bool:
        """Return True while the output stream is running."""
        return self._is_playing

    def start(self) -> None:
        """Open the output stream and start the callback.

        Raises:
            ImportError: sounddevice is not installed.
            sounddevice.PortAudioError: no usable output device or unsupported format.
        """
        # Imported lazily so this module stays importable (and testable) on machines without PortAudio.
        import sounddevice as sd  # imported lazily so this module stays

        # Restart from beat zero with no stale scheduled note-offs.
        self._sample_position = 0
        self._pending_note_offs = []
        self._is_playing = True
        # Float32 stereo at the engine rate; PortAudio calls self._callback on its own thread for every block.
        self._stream = sd.OutputStream(
            samplerate=self.sample_rate,
            channels=2,
            dtype="float32",
            blocksize=self.block_size,
            callback=self._callback,
        )
        self._stream.start()

    def stop(self) -> None:
        """Stop the stream, silence every voice and clear scheduled note-offs. Safe to call when already stopped."""
        self._is_playing = False
        # Stop and close first so the callback can no longer run while voices are being cleared below.
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        # choke_all_voices gives a short fade rather than an instant cut, which avoids a click.
        for track in self._tracks.values():
            track.choke_all_voices()
        self._pending_note_offs = []

    # -- the real-time callback ---------------------------------------

    def _apply_step_clock(self, frames: int) -> None:
        """Fire on_step_boundary for every sixteenth-note boundary that falls inside the coming block.

        Args:
            frames: size of the block about to be produced.

        Applies a pending tempo change at the first boundary it crosses.
        """
        step_frames = self._current_step_frames()
        start = self._sample_position
        end = start + frames
        # Ceiling division, not "next after start": a boundary landing
        # exactly on this block's start must still fire here. Using
        # ((start // step_frames) + 1) * step_frames instead would skip it
        # forever -- the following callback starts exactly at that same
        # boundary and would compute the *next* one, never reporting this
        # one at all. Found by test_step_boundary_fires_at_expected_sample_positions.
        # Ceiling division, not "next after start": a boundary landing
        # exactly on this block's start must still fire here. Using
        # ((start // step_frames) + 1) * step_frames instead would skip it
        # forever -- the following callback starts exactly at that same
        # boundary and would compute the *next* one, never reporting this
        # one at all. Found by test_step_boundary_fires_at_expected_sample_positions.
        next_boundary = ((start + step_frames - 1) // step_frames) * step_frames
        # More than one boundary can fall in a block only at very fast tempos or large blocks; the loop handles that.
        while next_boundary < end:
            # Tempo changes take effect on a boundary so a step is never lengthened or shortened mid-way.
            if self._pending_bpm is not None:
                self.bpm = self._pending_bpm
                self._pending_bpm = None
                step_frames = self._current_step_frames()
            # The step index is the boundary position divided by the step length (see the known-limit note about tempo changes).
            if self.on_step_boundary is not None:
                self.on_step_boundary(next_boundary // step_frames)
            next_boundary += step_frames

    def _apply_pending_note_offs(self) -> None:
        """Release every voice whose gate has expired.

        A voice's due frame is compared with the position AFTER the block just rendered, so a note-off can be up to one block (about 21 ms) late.
        """
        # Common case: nothing scheduled, so return before building a new list.
        if not self._pending_note_offs:
            return
        # Rebuild the list instead of removing while iterating.
        still_pending = []
        for due_sample, voice in self._pending_note_offs:
            if due_sample <= self._sample_position:
                voice.note_off()
            else:
                still_pending.append((due_sample, voice))
        self._pending_note_offs = still_pending

    # Which tracks to degrade: the shed level counts how many of the quietest tracks lose their compressor; levels beyond the track count also remove reverbs.
    def _shed_targets(self) -> tuple[set[str], set[str]]:
        """Deterministic overload shedding order from the Real-Time Safety
        Contract: lowest-peak-level tracks first, compressors before
        reverbs. Ranked on the *previous* block's peak levels -- this
        block's levels aren't known until after processing, which is the
        standard practical tradeoff for this kind of adaptive shedding."""
        # Not overloaded: bypass nothing.
        if self._shed_count <= 0:
            return set(), set()
        # Quietest first: removing effects from a track the user can barely hear costs the least audible quality.
        ranked = sorted(self._tracks.values(), key=lambda t: t.peak_level)
        ranked_ids = [t.track_id for t in ranked]
        n = len(ranked_ids)
        compressor_bypass = set(ranked_ids[: min(self._shed_count, n)])
        extra = max(0, self._shed_count - n)
        reverb_bypass = set(ranked_ids[: min(extra, n)])
        return compressor_bypass, reverb_bypass

    def _update_overload_state(self, elapsed_seconds: float, block_period_seconds: float, frames: int) -> None:
        """Update the overload counters after a block and tell the UI when shedding starts or stops.

        Args:
            elapsed_seconds: wall time the callback took.
            block_period_seconds: how long the block lasts (frames / sample_rate), i.e. the deadline.
            frames: block size, used to bank headroom.
        """
        # Late block: count it, lose any banked headroom, and after two in a row raise the shedding level by one.
        if elapsed_seconds > block_period_seconds * OVERLOAD_THRESHOLD_FRACTION:
            self._overload_consecutive += 1
            self._headroom_samples = 0
            if self._overload_consecutive >= OVERLOAD_CONSECUTIVE_BLOCKS:
                self._shed_count += 1
        else:
            # On time: reset the late streak and bank the frames; once one full second has been banked, restore one level (slow recovery avoids flapping).
            self._overload_consecutive = 0
            self._headroom_samples += frames
            if self._headroom_samples >= self.sample_rate and self._shed_count > 0:
                self._shed_count -= 1
                self._headroom_samples = 0

        # Notify only on transitions (shedding started or ended), not on every block.
        was_active = self._overload_active
        self._overload_active = self._shed_count > 0
        if self._overload_active != was_active and self.on_overload_changed is not None:
            self.on_overload_changed(self._overload_active)

    def _emit_meters_if_due(self, frames: int) -> None:
        """Send the meter dictionary to the UI at METER_EMIT_HZ.

        The dict maps track_id -> {"peak": linear peak level, "gain_reduction_db": compressor reduction in dB} and is built on the audio thread.
        """
        self._meter_frame_counter += frames
        if self._meter_frame_counter < self._meter_emit_interval_frames:
            return
        self._meter_frame_counter = 0
        if self.on_meter_update is not None:
            self.on_meter_update({
                track_id: {"peak": t.peak_level, "gain_reduction_db": t.gain_reduction_db}
                for track_id, t in self._tracks.items()
            })

    def _callback(self, outdata: np.ndarray, frames: int, time_info, status) -> None:
        """Sound-card callback: render one block of audio.

        Args:
            outdata: float32 array (frames, 2) that must be filled with the audio to play.
            frames: number of frames requested this call.
            time_info: PortAudio timing information (unused).
            status: PortAudio underrun/overrun flags (unused; see known limits).

        Runs on the audio thread: must be fast and must not raise.
        """
        # perf_counter measures how long rendering takes so overload can be detected.
        start_time = time.perf_counter()

        self._apply_step_clock(frames)

        # A view of the preallocated scratch area, cleared each block before tracks are added to it.
        master = self._master_scratch[:frames]
        master[:] = 0.0

        # Solo logic: if any track is soloed, every non-soloed track is skipped; muted tracks are always skipped.
        any_solo = any(t.soloed for t in self._tracks.values())
        compressor_bypass, reverb_bypass = self._shed_targets()
        for track_id, track in self._tracks.items():
            if track.muted:
                continue
            if any_solo and not track.soloed:
                continue
            contribution = track.process_block(
                frames,
                force_bypass_compressor=track_id in compressor_bypass,
                force_bypass_reverb=track_id in reverb_bypass,
            )
            master += contribution

        # Master fader (clamped to the +6 dB maximum) then the limiter, which guarantees the output never exceeds the ceiling.
        master *= db_to_linear(min(self.master_fader_db, MASTER_FADER_MAX_DB))
        self._limiter.process(master)
        outdata[:] = master

        # Advance the clock after rendering so step and note-off times line up with the block just played.
        self._sample_position += frames
        self._apply_pending_note_offs()

        # Time spent this block versus the time available.
        elapsed = time.perf_counter() - start_time
        self._update_overload_state(elapsed, frames / self.sample_rate, frames)
        self._emit_meters_if_due(frames)
