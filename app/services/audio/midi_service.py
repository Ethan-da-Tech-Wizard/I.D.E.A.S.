"""MIDI hardware bridge for the DAW step sequencer.

`python-rtmidi` is an optional dependency. When it is not installed, or no
compatible hardware is attached, device discovery degrades to an empty list
and the service logs a warning instead of raising -- the app must still start
and the DAW must still function with zero MIDI hardware present.
"""
from __future__ import annotations

import logging
from typing import Callable, Optional

from app.security import SecurityError

logger = logging.getLogger(__name__)

try:
    import rtmidi  # type: ignore[import-not-found]

    RTMIDI_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised via monkeypatch in tests
    rtmidi = None
    RTMIDI_AVAILABLE = False


NOTE_ON_STATUS = 0x90
NOTE_OFF_STATUS = 0x80


class MidiService:
    """Bridges a MIDI input device's note events to registered callbacks."""

    def __init__(self) -> None:
        self._midi_in = None
        self._connected_device: Optional[str] = None
        self._note_on_callbacks: list[Callable[[int, int], None]] = []
        self._note_off_callbacks: list[Callable[[int], None]] = []

    def list_devices(self) -> list[str]:
        if not RTMIDI_AVAILABLE:
            logger.warning("python-rtmidi is not installed; no MIDI devices available.")
            return []
        try:
            probe = rtmidi.MidiIn()
            try:
                return list(probe.get_ports())
            finally:
                del probe
        except Exception:
            logger.warning("Failed to query MIDI devices.", exc_info=True)
            return []

    def connect(self, device_name: str) -> bool:
        if not RTMIDI_AVAILABLE:
            logger.warning("Cannot connect to %s: python-rtmidi is not installed.", device_name)
            return False

        devices = self.list_devices()
        if device_name not in devices:
            return False

        try:
            midi_in = rtmidi.MidiIn()
            midi_in.open_port(devices.index(device_name))
            midi_in.set_callback(self._on_message)
        except Exception:
            logger.warning("Failed to connect to MIDI device %s.", device_name, exc_info=True)
            return False

        self.disconnect()
        self._midi_in = midi_in
        self._connected_device = device_name
        return True

    def disconnect(self) -> None:
        if self._midi_in is not None:
            try:
                self._midi_in.close_port()
            except Exception:
                logger.warning("Error while closing MIDI port.", exc_info=True)
        self._midi_in = None
        self._connected_device = None

    def is_connected(self) -> bool:
        return self._connected_device is not None

    def connected_device(self) -> Optional[str]:
        return self._connected_device

    def on_note_on(self, callback: Callable[[int, int], None]) -> None:
        self._note_on_callbacks.append(callback)

    def on_note_off(self, callback: Callable[[int], None]) -> None:
        self._note_off_callbacks.append(callback)

    def _on_message(self, event: tuple[list[int], float], data: object = None) -> None:
        message, _delta_time = event
        if not message:
            return
        status = message[0] & 0xF0
        if status == NOTE_ON_STATUS and len(message) >= 3:
            note, velocity = message[1], message[2]
            if velocity == 0:
                self._dispatch_note_off(note)
            else:
                self._dispatch_note_on(note, velocity)
        elif status == NOTE_OFF_STATUS and len(message) >= 2:
            self._dispatch_note_off(message[1])

    def _dispatch_note_on(self, note: int, velocity: int) -> None:
        for callback in self._note_on_callbacks:
            callback(note, velocity)

    def _dispatch_note_off(self, note: int) -> None:
        for callback in self._note_off_callbacks:
            callback(note)


DEFAULT_NOTE_MAPPING: dict[int, str] = {
    36: "kick",
    38: "snare",
    42: "hihat_closed",
    46: "hihat_open",
    49: "crash",
}


class MidiMapper:
    """Maps MIDI note numbers to DAW track/step trigger names."""

    def __init__(self, mapping: Optional[dict[int, str]] = None) -> None:
        self.mapping: dict[int, str] = dict(mapping) if mapping else dict(DEFAULT_NOTE_MAPPING)

    def map_note(self, note: int) -> Optional[str]:
        return self.mapping.get(note)

    def set_mapping(self, note: int, track_name: str) -> None:
        self.mapping[note] = track_name

    def remove_mapping(self, note: int) -> None:
        self.mapping.pop(note, None)

    def to_dict(self) -> dict[int, str]:
        return dict(self.mapping)


class MidiTrackRouter:
    """Pure note-to-track routing logic, kept separate from DawWorkspace/DawTrack
    so it is unit-testable without a QApplication.

    A MidiMapper resolves a raw note number to a semantic label ("kick",
    "snare", ...); this router then resolves that label to a concrete DAW
    track index, based on assignments the user makes at runtime (e.g. via a
    "learn" UI or an explicit assignment call). Notes with no mapper label, or
    labels with no track assignment yet, resolve to None and are silently
    ignored by the caller -- MIDI wiring must never crash playback just
    because a note is unmapped or hardware sends noise.
    """

    def __init__(self, mapper: MidiMapper) -> None:
        self.mapper = mapper
        self.assignments: dict[str, int] = {}

    def assign(self, label: str, track_index: int, track_count: int) -> None:
        if not (0 <= track_index < track_count):
            raise SecurityError(f"Track index {track_index} is out of range.")
        self.assignments[label] = track_index

    def unassign(self, label: str) -> None:
        self.assignments.pop(label, None)

    def resolve(self, note: int, velocity: int) -> Optional[int]:
        if velocity <= 0:
            return None
        label = self.mapper.map_note(note)
        if label is None:
            return None
        return self.assignments.get(label)
