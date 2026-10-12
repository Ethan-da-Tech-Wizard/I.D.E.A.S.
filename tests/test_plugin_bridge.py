from __future__ import annotations

from pathlib import Path

import pytest

from app.security import SecurityError
from app.services.audio.plugin_bridge import MAX_PLUGIN_SCAN_DIRS, PluginDescriptor, PluginHost


def make_descriptor(plugin_id: str = "p1", path: str = "vst3/synth.vst3") -> PluginDescriptor:
    return PluginDescriptor(
        plugin_id=plugin_id,
        name="Synth One",
        vendor="Acme Audio",
        format="VST3",
        path=path,
    )


def test_descriptor_creation_happy_path() -> None:
    descriptor = make_descriptor()
    assert descriptor.name == "Synth One"
    assert descriptor.num_inputs == 2
    assert descriptor.num_outputs == 2


def test_descriptor_rejects_unsupported_format() -> None:
    with pytest.raises(SecurityError):
        PluginDescriptor(
            plugin_id="p1", name="X", vendor="Y", format="LADSPA", path="plugins/x.so"
        )


def test_descriptor_rejects_negative_channel_counts() -> None:
    with pytest.raises(SecurityError):
        PluginDescriptor(
            plugin_id="p1", name="X", vendor="Y", format="VST3", path="p.vst3", num_inputs=-1
        )


def test_scan_plugins_returns_empty_stub(tmp_path: Path) -> None:
    (tmp_path / "vst3").mkdir()
    host = PluginHost(str(tmp_path))
    result = host.scan_plugins(["vst3"])
    assert result == []


def test_scan_plugins_rejects_path_traversal(tmp_path: Path) -> None:
    host = PluginHost(str(tmp_path))
    with pytest.raises(SecurityError):
        host.scan_plugins(["../../etc"])


def test_scan_plugins_rejects_too_many_dirs(tmp_path: Path) -> None:
    host = PluginHost(str(tmp_path))
    with pytest.raises(SecurityError):
        host.scan_plugins(["dir"] * (MAX_PLUGIN_SCAN_DIRS + 1))


def test_load_plugin_rejects_path_traversal(tmp_path: Path) -> None:
    host = PluginHost(str(tmp_path))
    descriptor = make_descriptor(path="../../etc/passwd")
    with pytest.raises(SecurityError):
        host.load_plugin(descriptor)


def test_load_and_unload_plugin(tmp_path: Path) -> None:
    (tmp_path / "vst3").mkdir()
    host = PluginHost(str(tmp_path))
    descriptor = make_descriptor()
    assert host.load_plugin(descriptor) is True
    assert host.is_loaded("p1") is True

    host.unload_plugin("p1")
    assert host.is_loaded("p1") is False


def test_process_block_pass_through(tmp_path: Path) -> None:
    host = PluginHost(str(tmp_path))
    descriptor = make_descriptor()
    host.load_plugin(descriptor)

    audio = b"\x00\x01\x02\x03"
    result = host.process_block("p1", audio, num_frames=1)
    assert result == audio


def test_process_block_rejects_unloaded_plugin(tmp_path: Path) -> None:
    host = PluginHost(str(tmp_path))
    with pytest.raises(SecurityError):
        host.process_block("missing", b"", num_frames=0)


def test_plugin_host_requires_directory_workspace(tmp_path: Path) -> None:
    not_a_dir = tmp_path / "file.txt"
    not_a_dir.write_text("x")
    with pytest.raises(SecurityError):
        PluginHost(str(not_a_dir))
