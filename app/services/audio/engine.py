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

DEFAULT_BLOCK_SIZE = 1024
GLOBAL_VOICE_CAP = 256
OVERLOAD_THRESHOLD_FRACTION = 0.70
OVERLOAD_CONSECUTIVE_BLOCKS = 2
METER_EMIT_HZ = 30
MASTER_FADER_MAX_DB = 6.0


class ChokeGroupRegistry:
    """Cross-track choke groups -- see "Voice & Polyphony Rules" in
    docs/DAW_VISION_SPEC.md. Deliberately outside TrackProcessor, which has
    no visibility into sibling tracks."""

    def __init__(self) -> None:
        self._groups: dict[int, list[TrackProcessor]] = {}

    def register(self, track: TrackProcessor) -> None:
        if track.choke_group is not None:
            self._groups.setdefault(track.choke_group, []).append(track)

    def unregister(self, track: TrackProcessor) -> None:
        if track.choke_group is not None:
            group = self._groups.get(track.choke_group)
            if group and track in group:
                group.remove(track)

    def notify_trigger(self, track: TrackProcessor) -> None:
        if track.choke_group is None:
            return
        for other in self._groups.get(track.choke_group, ()):
            if other is not track:
                other.choke_all_voices()


class AudioEngine:
    def __init__(self, sample_rate: int = SAMPLE_RATE, block_size: int = DEFAULT_BLOCK_SIZE,
                 max_block_size: int = 4096) -> None:
        self.sample_rate = sample_rate
        self.block_size = block_size
        self.bpm = 120.0
        self.master_fader_db = 0.0

        self._max_block_size = max_block_size
        self._tracks: dict[str, TrackProcessor] = {}
        self._choke_registry = ChokeGroupRegistry()
        self._master_scratch = np.zeros((max_block_size, 2), dtype=np.float32)
        self._limiter = Limiter(sample_rate)

        self._sample_position = 0
        self._is_playing = False
        self._pending_bpm: float | None = None
        self._pending_note_offs: list[tuple[int, Voice]] = []

        self._overload_consecutive = 0
        self._overload_active = False
        self._headroom_samples = 0
        self._shed_count = 0

        self._meter_emit_interval_frames = max(1, int(sample_rate / METER_EMIT_HZ))
        self._meter_frame_counter = 0

        self._stream = None  # sounddevice.OutputStream, created in start()

        # Plain-callable hooks -- see module docstring re: Qt.
        self.on_step_boundary: Callable[[int], None] | None = None
        self.on_meter_update: Callable[[dict], None] | None = None
        self.on_overload_changed: Callable[[bool], None] | None = None

    # -- track management ---------------------------------------------

    def add_track(self, track: TrackProcessor) -> None:
        self._tracks[track.track_id] = track
        self._choke_registry.register(track)

    def remove_track(self, track_id: str) -> None:
        track = self._tracks.pop(track_id, None)
        if track is not None:
            self._choke_registry.unregister(track)

    def _total_voice_count(self) -> int:
        return sum(t.voice_count for t in self._tracks.values())

    # -- triggering ------------------------------------------------------

    def _current_step_frames(self) -> int:
        """16th-note step length in samples, matching the existing
        AudioEngineThread's `(60.0 / bpm) / 4.0` interval convention in
        app/ui/daw.py."""
        return max(1, int(self.sample_rate * (60.0 / self.bpm) / 4.0))

    def trigger_step_note(self, track_id: str, gate_length_pct: float) -> Voice | None:
        """Fires a step-sequencer note with a scheduled, gate-length-derived
        note-off -- see "Step sequencer gate model" in
        docs/DAW_VISION_SPEC.md."""
        if self._total_voice_count() >= GLOBAL_VOICE_CAP:
            return None
        track = self._tracks.get(track_id)
        if track is None:
            return None
        voice = track.trigger_note()
        self._choke_registry.notify_trigger(track)
        gate_frames = max(1, int(self._current_step_frames() * (gate_length_pct / 100.0)))
        self._pending_note_offs.append((self._sample_position + gate_frames, voice))
        return voice

    def note_on(self, track_id: str) -> Voice | None:
        """For MIDI-held notes: no scheduled note-off, caller holds the
        returned Voice and calls note_off_voice() on physical key-up."""
        if self._total_voice_count() >= GLOBAL_VOICE_CAP:
            return None
        track = self._tracks.get(track_id)
        if track is None:
            return None
        voice = track.trigger_note()
        self._choke_registry.notify_trigger(track)
        return voice

    def note_off_voice(self, voice: Voice) -> None:
        voice.note_off()

    # -- transport ---------------------------------------------------

    def set_bpm(self, bpm: float) -> None:
        """Quantized to the next step boundary, not applied mid-step -- see
        the Real-Time Safety Contract."""
        self._pending_bpm = max(20.0, bpm)

    def is_playing(self) -> bool:
        return self._is_playing

    def start(self) -> None:
        import sounddevice as sd  # imported lazily so this module stays

        self._sample_position = 0
        self._pending_note_offs = []
        self._is_playing = True
        self._stream = sd.OutputStream(
            samplerate=self.sample_rate,
            channels=2,
            dtype="float32",
            blocksize=self.block_size,
            callback=self._callback,
        )
        self._stream.start()

    def stop(self) -> None:
        self._is_playing = False
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        for track in self._tracks.values():
            track.choke_all_voices()
        self._pending_note_offs = []

    # -- the real-time callback ---------------------------------------

    def _apply_step_clock(self, frames: int) -> None:
        step_frames = self._current_step_frames()
        start = self._sample_position
        end = start + frames
        # Ceiling division, not "next after start": a boundary landing
        # exactly on this block's start must still fire here. Using
        # ((start // step_frames) + 1) * step_frames instead would skip it
        # forever -- the following callback starts exactly at that same
        # boundary and would compute the *next* one, never reporting this
        # one at all. Found by test_step_boundary_fires_at_expected_sample_positions.
        next_boundary = ((start + step_frames - 1) // step_frames) * step_frames
        while next_boundary < end:
            if self._pending_bpm is not None:
                self.bpm = self._pending_bpm
                self._pending_bpm = None
                step_frames = self._current_step_frames()
            if self.on_step_boundary is not None:
                self.on_step_boundary(next_boundary // step_frames)
            next_boundary += step_frames

    def _apply_pending_note_offs(self) -> None:
        if not self._pending_note_offs:
            return
        still_pending = []
        for due_sample, voice in self._pending_note_offs:
            if due_sample <= self._sample_position:
                voice.note_off()
            else:
                still_pending.append((due_sample, voice))
        self._pending_note_offs = still_pending

    def _shed_targets(self) -> tuple[set[str], set[str]]:
        """Deterministic overload shedding order from the Real-Time Safety
        Contract: lowest-peak-level tracks first, compressors before
        reverbs. Ranked on the *previous* block's peak levels -- this
        block's levels aren't known until after processing, which is the
        standard practical tradeoff for this kind of adaptive shedding."""
        if self._shed_count <= 0:
            return set(), set()
        ranked = sorted(self._tracks.values(), key=lambda t: t.peak_level)
        ranked_ids = [t.track_id for t in ranked]
        n = len(ranked_ids)
        compressor_bypass = set(ranked_ids[: min(self._shed_count, n)])
        extra = max(0, self._shed_count - n)
        reverb_bypass = set(ranked_ids[: min(extra, n)])
        return compressor_bypass, reverb_bypass

    def _update_overload_state(self, elapsed_seconds: float, block_period_seconds: float, frames: int) -> None:
        if elapsed_seconds > block_period_seconds * OVERLOAD_THRESHOLD_FRACTION:
            self._overload_consecutive += 1
            self._headroom_samples = 0
            if self._overload_consecutive >= OVERLOAD_CONSECUTIVE_BLOCKS:
                self._shed_count += 1
        else:
            self._overload_consecutive = 0
            self._headroom_samples += frames
            if self._headroom_samples >= self.sample_rate and self._shed_count > 0:
                self._shed_count -= 1
                self._headroom_samples = 0

        was_active = self._overload_active
        self._overload_active = self._shed_count > 0
        if self._overload_active != was_active and self.on_overload_changed is not None:
            self.on_overload_changed(self._overload_active)

    def _emit_meters_if_due(self, frames: int) -> None:
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
        start_time = time.perf_counter()

        self._apply_step_clock(frames)

        master = self._master_scratch[:frames]
        master[:] = 0.0

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

        master *= db_to_linear(min(self.master_fader_db, MASTER_FADER_MAX_DB))
        self._limiter.process(master)
        outdata[:] = master

        self._sample_position += frames
        self._apply_pending_note_offs()

        elapsed = time.perf_counter() - start_time
        self._update_overload_state(elapsed, frames / self.sample_rate, frames)
        self._emit_meters_if_due(frames)
