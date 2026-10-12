"""Architecture-only surface for future VST3/AU plugin hosting.

Native plugin loading, parameter automation, and real-time DSP processing are
out of scope until a sandboxed, reviewed plugin loader exists (see
THE_IDEAS_MANIFEST.md's FAIL-CLOSED ARCHITECTURE directive). This module
defines and path-validates the data model and API surface that future work
will implement against: scan_plugins, load_plugin, and process_block are all
stubbed today.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from app.security import SecurityError
from app.storage.workspace import is_safe_subpath

MAX_PLUGIN_SCAN_DIRS = 32
SUPPORTED_PLUGIN_FORMATS = ("VST3", "AU")


@dataclass(frozen=True, slots=True)
class PluginDescriptor:
    """Describes a single audio plugin binary discovered on disk."""

    plugin_id: str
    name: str
    vendor: str
    format: str
    path: str
    num_inputs: int = 2
    num_outputs: int = 2

    def __post_init__(self) -> None:
        if self.format not in SUPPORTED_PLUGIN_FORMATS:
            raise SecurityError(f"Unsupported plugin format: {self.format!r}.")
        if self.num_inputs < 0 or self.num_outputs < 0:
            raise SecurityError("Plugin channel counts cannot be negative.")


class PluginHost:
    """Stubbed host for future VST3/AU plugin loading and audio processing.

    All plugin and search-directory paths are validated to stay within the
    workspace passed at construction time, the same boundary every other
    file-touching service in this app enforces via is_safe_subpath().
    """

    def __init__(self, workspace_path: str) -> None:
        if not os.path.isdir(workspace_path):
            raise SecurityError("Plugin host workspace path must be a directory.")
        self.workspace_path = os.path.abspath(workspace_path)
        self._loaded: dict[str, PluginDescriptor] = {}

    def _resolve_within_workspace(self, path: str) -> str:
        candidate = os.path.abspath(os.path.join(self.workspace_path, path))
        if not is_safe_subpath(self.workspace_path, candidate):
            raise SecurityError("Path escapes the plugin host workspace.")
        return candidate

    def scan_plugins(self, search_dirs: list[str]) -> list[PluginDescriptor]:
        """Stub: validates search directories and returns no plugins yet."""
        if len(search_dirs) > MAX_PLUGIN_SCAN_DIRS:
            raise SecurityError("Too many plugin search directories.")
        for search_dir in search_dirs:
            self._resolve_within_workspace(search_dir)
        return []

    def load_plugin(self, descriptor: PluginDescriptor) -> bool:
        """Stub: validates the descriptor's path and records it as loaded."""
        self._resolve_within_workspace(descriptor.path)
        self._loaded[descriptor.plugin_id] = descriptor
        return True

    def unload_plugin(self, plugin_id: str) -> None:
        self._loaded.pop(plugin_id, None)

    def is_loaded(self, plugin_id: str) -> bool:
        return plugin_id in self._loaded

    def process_block(self, plugin_id: str, audio_block: bytes, num_frames: int) -> bytes:
        """Stub: pass-through until a real DSP bridge exists."""
        if plugin_id not in self._loaded:
            raise SecurityError("Cannot process audio for a plugin that is not loaded.")
        if num_frames < 0:
            raise SecurityError("num_frames cannot be negative.")
        return audio_block
