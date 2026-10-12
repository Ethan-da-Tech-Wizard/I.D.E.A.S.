from __future__ import annotations

import pytest

from app.security import SecurityError
from app.services.audio import midi_service
from app.services.audio.midi_service import DEFAULT_NOTE_MAPPING, MidiMapper, MidiService, MidiTrackRouter


class FakeMidiIn:
    """Minimal stand-in for rtmidi.MidiIn used to test the connect/callback path
    without requiring the optional python-rtmidi dependency or real hardware."""

    ports = ["Launchpad Mini", "MPK Mini"]
    instances: list["FakeMidiIn"] = []

    def __init__(self) -> None:
        self.callback = None
        self.opened_port = None
        self.closed = False
        FakeMidiIn.instances.append(self)

    def get_ports(self) -> list[str]:
        return list(self.ports)

    def open_port(self, index: int) -> None:
        self.opened_port = index

    def set_callback(self, callback) -> None:
        self.callback = callback

    def close_port(self) -> None:
        self.closed = True


class FakeRtmidi:
    MidiIn = FakeMidiIn


@pytest.fixture
def fake_hardware(monkeypatch):
    FakeMidiIn.instances = []
    monkeypatch.setattr(midi_service, "rtmidi", FakeRtmidi)
    monkeypatch.setattr(midi_service, "RTMIDI_AVAILABLE", True)
    yield FakeMidiIn


def test_list_devices_returns_empty_without_hardware() -> None:
    service = MidiService()
    assert service.list_devices() == []
    assert service.is_connected() is False


def test_connect_without_rtmidi_fails_gracefully() -> None:
    service = MidiService()
    assert service.connect("Anything") is False
    assert service.is_connected() is False


def test_list_devices_with_fake_hardware(fake_hardware) -> None:
    service = MidiService()
    assert service.list_devices() == ["Launchpad Mini", "MPK Mini"]


def test_connect_to_known_device_with_fake_hardware(fake_hardware) -> None:
    service = MidiService()
    assert service.connect("MPK Mini") is True
    assert service.is_connected() is True
    assert service.connected_device() == "MPK Mini"


def test_connect_to_unknown_device_fails(fake_hardware) -> None:
    service = MidiService()
    assert service.connect("Nonexistent Device") is False
    assert service.is_connected() is False


def test_disconnect_closes_port(fake_hardware) -> None:
    service = MidiService()
    service.connect("MPK Mini")
    service.disconnect()
    assert service.is_connected() is False
    assert fake_hardware.instances[-1].closed is True


def test_note_on_and_off_callbacks_dispatch(fake_hardware) -> None:
    service = MidiService()
    service.connect("MPK Mini")

    seen_on: list[tuple[int, int]] = []
    seen_off: list[int] = []
    service.on_note_on(lambda note, velocity: seen_on.append((note, velocity)))
    service.on_note_off(lambda note: seen_off.append(note))

    handle = fake_hardware.instances[-1]
    handle.callback(([0x90, 36, 100], 0.0))
    handle.callback(([0x80, 36, 0], 0.0))
    # Note-on with velocity 0 is a note-off per the MIDI spec
    handle.callback(([0x90, 38, 0], 0.0))

    assert seen_on == [(36, 100)]
    assert seen_off == [36, 38]


def test_midi_mapper_defaults_and_overrides() -> None:
    mapper = MidiMapper()
    assert mapper.map_note(36) == "kick"
    assert mapper.to_dict() == DEFAULT_NOTE_MAPPING

    mapper.set_mapping(60, "custom_track")
    assert mapper.map_note(60) == "custom_track"

    mapper.remove_mapping(36)
    assert mapper.map_note(36) is None


def test_midi_mapper_unknown_note_returns_none() -> None:
    mapper = MidiMapper()
    assert mapper.map_note(127) is None


def test_router_resolves_mapped_note_to_assigned_track() -> None:
    router = MidiTrackRouter(MidiMapper())
    router.assign("kick", track_index=0, track_count=4)
    router.assign("snare", track_index=1, track_count=4)

    assert router.resolve(36, velocity=100) == 0  # 36 -> "kick"
    assert router.resolve(38, velocity=100) == 1  # 38 -> "snare"


def test_router_returns_none_for_unassigned_label() -> None:
    router = MidiTrackRouter(MidiMapper())
    # 42 maps to "hihat_closed" by default, but nothing has been assigned to it.
    assert router.resolve(42, velocity=100) is None


def test_router_returns_none_for_unmapped_note() -> None:
    router = MidiTrackRouter(MidiMapper())
    router.assign("kick", track_index=0, track_count=4)
    assert router.resolve(127, velocity=100) is None


def test_router_ignores_zero_velocity_note_off() -> None:
    router = MidiTrackRouter(MidiMapper())
    router.assign("kick", track_index=0, track_count=4)
    assert router.resolve(36, velocity=0) is None


def test_router_assign_rejects_track_index_out_of_range() -> None:
    router = MidiTrackRouter(MidiMapper())
    with pytest.raises(SecurityError):
        router.assign("kick", track_index=5, track_count=4)


def test_router_unassign_removes_mapping() -> None:
    router = MidiTrackRouter(MidiMapper())
    router.assign("kick", track_index=0, track_count=4)
    router.unassign("kick")
    assert router.resolve(36, velocity=100) is None
