# ==============================================================================
# Program: tests/test_plugin_bridge.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: tests app/services/audio/plugin_bridge.py, the (currently stubbed) host for third-party
# audio plugins. The point of the tests is the security envelope that must exist BEFORE real
# plugin loading is added: only known formats, bounded scan lists, workspace-confined paths.
# Scanning finds nothing and processing is a pass-through because no plugin SDK is integrated yet.
# Run: python3 -m pytest tests/test_plugin_bridge.py -q
# ==============================================================================

from __future__ import annotations

from pathlib import Path

import pytest

from app.security import SecurityError
from app.services.audio.plugin_bridge import MAX_PLUGIN_SCAN_DIRS, PluginDescriptor, PluginHost


# Helper: a valid descriptor for a fictional VST3 synth, with overridable id and path.
def make_descriptor(plugin_id: str = "p1", path: str = "vst3/synth.vst3") -> PluginDescriptor:
    """Return a PluginDescriptor with sensible defaults for tests."""
    return PluginDescriptor(
        plugin_id=plugin_id,
        name="Synth One",
        vendor="Acme Audio",
        format="VST3",
        path=path,
    )


# Defaults: stereo in and out (2 and 2) when channel counts are not given.
def test_descriptor_creation_happy_path() -> None:
    descriptor = make_descriptor()
    assert descriptor.name == "Synth One"
    assert descriptor.num_inputs == 2
    assert descriptor.num_outputs == 2


# Only supported plugin formats are accepted; LADSPA is not on the list.
def test_descriptor_rejects_unsupported_format() -> None:
    with pytest.raises(SecurityError):
        PluginDescriptor(
            plugin_id="p1", name="X", vendor="Y", format="LADSPA", path="plugins/x.so"
        )


# Negative channel counts are nonsense and would break buffer sizing later.
def test_descriptor_rejects_negative_channel_counts() -> None:
    with pytest.raises(SecurityError):
        PluginDescriptor(
            plugin_id="p1", name="X", vendor="Y", format="VST3", path="p.vst3", num_inputs=-1
        )


# Stub behaviour: scanning an existing in-workspace folder returns no plugins (nothing is loaded or executed).
def test_scan_plugins_returns_empty_stub(tmp_path: Path) -> None:
    (tmp_path / "vst3").mkdir()
    host = PluginHost(str(tmp_path))
    result = host.scan_plugins(["vst3"])
    assert result == []


# Scan folders must stay inside the workspace; "../../etc" is refused.
def test_scan_plugins_rejects_path_traversal(tmp_path: Path) -> None:
    host = PluginHost(str(tmp_path))
    with pytest.raises(SecurityError):
        host.scan_plugins(["../../etc"])


# MAX_PLUGIN_SCAN_DIRS bounds the work a caller can request.
def test_scan_plugins_rejects_too_many_dirs(tmp_path: Path) -> None:
    host = PluginHost(str(tmp_path))
    with pytest.raises(SecurityError):
        host.scan_plugins(["dir"] * (MAX_PLUGIN_SCAN_DIRS + 1))


# Loading would eventually execute native code from the descriptor's path, so a path outside the workspace is refused up front.
def test_load_plugin_rejects_path_traversal(tmp_path: Path) -> None:
    host = PluginHost(str(tmp_path))
    descriptor = make_descriptor(path="../../etc/passwd")
    with pytest.raises(SecurityError):
        host.load_plugin(descriptor)


# Lifecycle: load marks the plugin loaded, unload clears it.
def test_load_and_unload_plugin(tmp_path: Path) -> None:
    (tmp_path / "vst3").mkdir()
    host = PluginHost(str(tmp_path))
    descriptor = make_descriptor()
    assert host.load_plugin(descriptor) is True
    assert host.is_loaded("p1") is True

    host.unload_plugin("p1")
    assert host.is_loaded("p1") is False


# Stub processing returns the input bytes unchanged (a safe no-op until real DSP hosting exists).
def test_process_block_pass_through(tmp_path: Path) -> None:
    host = PluginHost(str(tmp_path))
    descriptor = make_descriptor()
    host.load_plugin(descriptor)

    audio = b"\x00\x01\x02\x03"
    result = host.process_block("p1", audio, num_frames=1)
    assert result == audio


# Processing with an id that was never loaded raises instead of silently returning data.
def test_process_block_rejects_unloaded_plugin(tmp_path: Path) -> None:
    host = PluginHost(str(tmp_path))
    with pytest.raises(SecurityError):
        host.process_block("missing", b"", num_frames=0)


# The host's root must be a directory, not a file.
def test_plugin_host_requires_directory_workspace(tmp_path: Path) -> None:
    not_a_dir = tmp_path / "file.txt"
    not_a_dir.write_text("x")
    with pytest.raises(SecurityError):
        PluginHost(str(not_a_dir))
