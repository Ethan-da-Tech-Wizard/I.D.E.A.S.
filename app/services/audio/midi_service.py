# ==============================================================================
# Program: midi_service.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: Parses, records, and dispatches MIDI note and control change messages from connected physical controllers to virtual synth instruments and DAW tracks.
# ==============================================================================
# How this module fits: app/ui/daw.py creates a MidiService (hardware input), a MidiMapper (note number -> drum name) and a MidiTrackRouter
# (drum name -> track index). A MIDI key press travels: rtmidi thread -> MidiService._on_message -> registered note-on callback ->
# router.resolve(note, velocity) -> track index -> the DAW triggers that track. tests/test_midi_service.py covers all three classes
# without hardware because the router and mapper are pure Python.
# Inputs: raw 3-byte MIDI messages from python-rtmidi (optional dependency). Outputs: calls to the registered callbacks.
# Design decisions: every failure mode (library missing, no device, driver error) is logged and turned into a harmless "no devices" result, because the DAW
# must start and work without MIDI hardware; only note on/off are handled (no pitch bend, control change or aftertouch).
# Known limits: the MIDI channel is ignored (a note on any of the 16 channels triggers the same track); callbacks run on rtmidi's own thread, so UI code
# must not touch widgets directly from them; the callback lists are plain lists mutated from the GUI thread without a lock; an exception inside a user callback
# propagates into the rtmidi thread.

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

# Optional import: RTMIDI_AVAILABLE lets the rest of the module (and tests) check support instead of crashing at import time.
try:
    import rtmidi  # type: ignore[import-not-found]

    RTMIDI_AVAILABLE = True
# The no-cover marker is there because the missing-library branch only runs on machines without rtmidi; tests simulate it by monkeypatching.
except ImportError:  # pragma: no cover - exercised via monkeypatch in tests
    rtmidi = None
    RTMIDI_AVAILABLE = False


# MIDI status bytes: the high nibble is the message type (0x9 note on, 0x8 note off), the low nibble is the channel 0-15.
NOTE_ON_STATUS = 0x90
NOTE_OFF_STATUS = 0x80


class MidiService:
    """Wraps one open MIDI input port and fans note events out to callbacks.

    Typical use: devices = list_devices(); connect(devices[0]); on_note_on(handler). Only one device is connected at a time.
    """

    def __init__(self) -> None:
        """Start with no port open and no listeners."""
        self._midi_in = None
        self._connected_device: Optional[str] = None
        self._note_on_callbacks: list[Callable[[int, int], None]] = []
        self._note_off_callbacks: list[Callable[[int], None]] = []

    def list_devices(self) -> list[str]:
        """List the names of attached MIDI input ports.

        Returns:
            Port names in the order rtmidi reports them, or [] when rtmidi is missing, nothing is attached or the query fails (a warning is logged).
        """
        if not RTMIDI_AVAILABLE:
            logger.warning("python-rtmidi is not installed; no MIDI devices available.")
            return []
        try:
            # A temporary MidiIn object is created only to read the port list; it is released immediately so it does not hold a port open.
            probe = rtmidi.MidiIn()
            try:
                return list(probe.get_ports())
            finally:
                # Dropping the reference closes the probe's native resources.
                del probe
        # Any driver error (no ALSA sequencer, permissions) becomes an empty list; the exception is logged with its traceback for diagnosis.
        except Exception:
            logger.warning("Failed to query MIDI devices.", exc_info=True)
            return []

    def connect(self, device_name: str) -> bool:
        """Open a named input port and start receiving notes.

        Args:
            device_name: one of the strings from list_devices().

        Returns:
            True on success; False if rtmidi is missing, the name is not currently attached, or opening failed.
        """
        if not RTMIDI_AVAILABLE:
            logger.warning("Cannot connect to %s: python-rtmidi is not installed.", device_name)
            return False

        # Look the name up again now (devices can be unplugged between listing and connecting); the list index is also the rtmidi port number.
        devices = self.list_devices()
        if device_name not in devices:
            return False

        # The new port is opened and wired to _on_message BEFORE the old one is closed, so a failed connect keeps the current device.
        try:
            midi_in = rtmidi.MidiIn()
            midi_in.open_port(devices.index(device_name))
            midi_in.set_callback(self._on_message)
        except Exception:
            logger.warning("Failed to connect to MIDI device %s.", device_name, exc_info=True)
            return False

        # Only after the new port works do we close the previous one.
        self.disconnect()
        self._midi_in = midi_in
        self._connected_device = device_name
        return True

    def disconnect(self) -> None:
        """Close the open port, if any, and forget the device. Safe to call repeatedly; a close error is logged, not raised."""
        if self._midi_in is not None:
            try:
                self._midi_in.close_port()
            except Exception:
                logger.warning("Error while closing MIDI port.", exc_info=True)
        self._midi_in = None
        self._connected_device = None

    def is_connected(self) -> bool:
        """Return True while a device is connected."""
        return self._connected_device is not None

    def connected_device(self) -> Optional[str]:
        """Return the connected device name, or None."""
        return self._connected_device

    def on_note_on(self, callback: Callable[[int, int], None]) -> None:
        """Register a function called as callback(note, velocity) for every note-on.

        Args:
            callback: note is 0-127, velocity 1-127. Called on the MIDI thread.
        """
        self._note_on_callbacks.append(callback)

    def on_note_off(self, callback: Callable[[int], None]) -> None:
        """Register a function called as callback(note) for every note-off (including note-on with velocity 0)."""
        self._note_off_callbacks.append(callback)

    def _on_message(self, event: tuple[list[int], float], data: object = None) -> None:
        """rtmidi callback: decode one raw message and dispatch it.

        Args:
            event: (message_bytes, seconds_since_previous_message) as supplied by rtmidi.
            data: user data pointer from rtmidi (unused).
        """
        message, _delta_time = event
        # Empty message: nothing to decode.
        if not message:
            return
        # Mask off the channel nibble to keep only the message type.
        status = message[0] & 0xF0
        if status == NOTE_ON_STATUS and len(message) >= 3:
            note, velocity = message[1], message[2]
            # A note-on with velocity 0 is the standard running-status way of sending note-off, so treat it as one.
            if velocity == 0:
                self._dispatch_note_off(note)
            else:
                self._dispatch_note_on(note, velocity)
        # Explicit note-off messages carry the note in byte 1; the release velocity in byte 2 is ignored.
        elif status == NOTE_OFF_STATUS and len(message) >= 2:
            self._dispatch_note_off(message[1])

    def _dispatch_note_on(self, note: int, velocity: int) -> None:
        """Call every note-on listener in registration order."""
        for callback in self._note_on_callbacks:
            callback(note, velocity)

    def _dispatch_note_off(self, note: int) -> None:
        """Call every note-off listener in registration order."""
        for callback in self._note_off_callbacks:
            callback(note)


# Default General MIDI drum map: 36 kick, 38 snare, 42 closed hi-hat, 46 open hi-hat, 49 crash cymbal (standard GM percussion note numbers, and the
# layout most pad controllers use).
DEFAULT_NOTE_MAPPING: dict[int, str] = {
    36: "kick",
    38: "snare",
    42: "hihat_closed",
    46: "hihat_open",
    49: "crash",
}


class MidiMapper:
    """Translates MIDI note numbers into drum names such as "kick" and "snare".

    The mapping is a plain dict the UI may edit (set_mapping/remove_mapping) and save (to_dict).
    """

    def __init__(self, mapping: Optional[dict[int, str]] = None) -> None:
        """Create a mapper.

        Args:
            mapping: custom note -> name table; a copy is stored. None or an empty dict falls back to a copy of DEFAULT_NOTE_MAPPING.
        """
        self.mapping: dict[int, str] = dict(mapping) if mapping else dict(DEFAULT_NOTE_MAPPING)

    def map_note(self, note: int) -> Optional[str]:
        """Return the drum name for a note, or None if the note is not mapped."""
        return self.mapping.get(note)

    def set_mapping(self, note: int, track_name: str) -> None:
        """Map `note` (0-127) to a drum name, replacing any earlier entry."""
        self.mapping[note] = track_name

    def remove_mapping(self, note: int) -> None:
        """Remove a note's mapping; unmapped notes are ignored without error."""
        self.mapping.pop(note, None)

    def to_dict(self) -> dict[int, str]:
        """Return a copy of the table so callers cannot alter the mapper's internal dict."""
        return dict(self.mapping)


class MidiTrackRouter:
    """Pure note-to-track routing logic, kept separate from DawWorkspace/DawTrack so it is unit-testable without a QApplication.

    A MidiMapper resolves a raw note number to a semantic label ("kick", "snare", ...); this router then resolves that label to a concrete DAW
    track index, based on assignments the user makes at runtime (e.g. via a "learn" UI or an explicit assignment call). Notes with no mapper label, or
    labels with no track assignment yet, resolve to None and are silently ignored by the caller -- MIDI wiring must never crash playback just
    because a note is unmapped or hardware sends noise.
    """

    def __init__(self, mapper: MidiMapper) -> None:
        """Create a router.

        Args:
            mapper: the MidiMapper used to turn notes into labels.
        """
        self.mapper = mapper
        self.assignments: dict[str, int] = {}

    def assign(self, label: str, track_index: int, track_count: int) -> None:
        """Assign a drum label to a track.

        Args:
            label: name from the mapper, e.g. "kick".
            track_index: 0-based index of the DAW track.
            track_count: number of tracks that currently exist (the valid range is 0 .. track_count-1).

        Raises:
            SecurityError: the index is outside the valid range.
        """
        if not (0 <= track_index < track_count):
            raise SecurityError(f"Track index {track_index} is out of range.")
        self.assignments[label] = track_index

    def unassign(self, label: str) -> None:
        """Remove a label's track assignment (no error if none)."""
        self.assignments.pop(label, None)

    def resolve(self, note: int, velocity: int) -> Optional[int]:
        """Decide which track a MIDI note should trigger.

        Args:
            note: MIDI note number.
            velocity: 1-127 for a real hit; 0 or less means note-off and is ignored.

        Returns:
            The track index, or None if the note is a release, unmapped or the label has no track yet.
        """
        if velocity <= 0:
            return None
        label = self.mapper.map_note(note)
        if label is None:
            return None
        return self.assignments.get(label)
