# ==============================================================================
# Program: plugin_bridge.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: Discovers, sandboxes, and hosts external VST3/AU audio plugins, managing parameter automation, audio buffer exchange, and plugin GUI embedding.
# ==============================================================================
# How this module fits: it is a placeholder for a future plugin feature. Nothing in the running app loads real VST3/AU binaries or instantiates a PluginHost yet
# (app/widgets/fx_rack.py only points at this file in its "plugins are architected" text); tests exercise it so the final shape of the API is fixed early. What it does today is validation only:
# every plugin or search path must stay inside the workspace folder (using storage.workspace.is_safe_subpath), counts are bounded, and formats
# are whitelisted, so when real loading arrives the security boundary already exists. tests/test_plugin_bridge.py checks those rules.
# Inputs: a workspace directory, PluginDescriptor values. Outputs: validated descriptors, an empty scan result, and unchanged audio.
# Design decisions: "fail closed" (the project's security stance): unknown formats, negative channel counts, escaping paths and processing of
# unloaded plugins all raise SecurityError rather than being tolerated.
# Known limits: scan_plugins always returns []; load_plugin returns True without opening any file (it does not check the file exists);
# process_block is a pass-through; is_safe_subpath compares text and does not resolve symlinks, so a symlink inside the workspace pointing outside it is accepted.

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

# At most 32 search folders per scan, a limit that keeps a hostile or corrupt project from making the scanner walk the whole disk. Only the two formats the project names are accepted.
MAX_PLUGIN_SCAN_DIRS = 32
SUPPORTED_PLUGIN_FORMATS = ("VST3", "AU")


# Frozen (immutable) and slotted (smaller, no stray attributes) so a descriptor cannot be changed after it has been validated.
@dataclass(frozen=True, slots=True)
class PluginDescriptor:
    """Immutable record describing one plugin binary.

    Attributes:
        plugin_id: unique key (used to load/unload/process).
        name, vendor: display strings.
        format: "VST3" or "AU" (validated in __post_init__).
        path: file location, expected inside the workspace.
        num_inputs, num_outputs: audio channel counts (default stereo, 2 in / 2 out); must not be negative.
    """

    plugin_id: str
    name: str
    vendor: str
    format: str
    path: str
    num_inputs: int = 2
    num_outputs: int = 2

    def __post_init__(self) -> None:
        """Validate the fields right after construction.

        Raises:
            SecurityError: unsupported format or negative channel count.
        """
        if self.format not in SUPPORTED_PLUGIN_FORMATS:
            raise SecurityError(f"Unsupported plugin format: {self.format!r}.")
        if self.num_inputs < 0 or self.num_outputs < 0:
            raise SecurityError("Plugin channel counts cannot be negative.")


class PluginHost:
    """Stub host for loading plugins and passing audio through them; see the module header for what is and is not implemented.

    All plugin and search-directory paths are validated to stay within the workspace passed at construction time, the same boundary every other
    file-touching service in this app enforces via is_safe_subpath().
    """

    def __init__(self, workspace_path: str) -> None:
        """Create a host bound to one workspace folder.

        Args:
            workspace_path: existing directory; every plugin path is resolved relative to it.

        Raises:
            SecurityError: the path is not a directory.
        """
        if not os.path.isdir(workspace_path):
            raise SecurityError("Plugin host workspace path must be a directory.")
        # Store the absolute path so later comparisons do not depend on the current working directory.
        self.workspace_path = os.path.abspath(workspace_path)
        # Registry of loaded plugins keyed by plugin_id.
        self._loaded: dict[str, PluginDescriptor] = {}

    def _resolve_within_workspace(self, path: str) -> str:
        """Turn a user-supplied path into an absolute path and make sure it stays inside the workspace.

        Args:
            path: relative (normal) or absolute path.

        Returns:
            The absolute path.

        Raises:
            SecurityError: the resolved path leaves the workspace folder.
        """
        # join() with an absolute `path` discards the workspace prefix, and abspath() collapses ".." parts; the containment test below catches both tricks.
        candidate = os.path.abspath(os.path.join(self.workspace_path, path))
        if not is_safe_subpath(self.workspace_path, candidate):
            raise SecurityError("Path escapes the plugin host workspace.")
        return candidate

    def scan_plugins(self, search_dirs: list[str]) -> list[PluginDescriptor]:
        """Check the folders to scan and return the plugins found.

        Args:
            search_dirs: folders (inside the workspace) that would be searched.

        Returns:
            Always an empty list for now.

        Raises:
            SecurityError: too many folders, or one of them escapes the workspace.
        """
        if len(search_dirs) > MAX_PLUGIN_SCAN_DIRS:
            raise SecurityError("Too many plugin search directories.")
        # Validate every folder even though nothing is scanned yet, so callers learn about bad input today rather than when scanning is implemented.
        for search_dir in search_dirs:
            self._resolve_within_workspace(search_dir)
        return []

    def load_plugin(self, descriptor: PluginDescriptor) -> bool:
        """Register a plugin after validating its path.

        Args:
            descriptor: the plugin to load.

        Returns:
            True (stub: nothing is actually opened).

        Raises:
            SecurityError: the descriptor's path escapes the workspace.
        """
        self._resolve_within_workspace(descriptor.path)
        self._loaded[descriptor.plugin_id] = descriptor
        return True

    def unload_plugin(self, plugin_id: str) -> None:
        """Forget a loaded plugin; unknown ids are ignored."""
        self._loaded.pop(plugin_id, None)

    def is_loaded(self, plugin_id: str) -> bool:
        """Return True if plugin_id has been loaded."""
        return plugin_id in self._loaded

    def process_block(self, plugin_id: str, audio_block: bytes, num_frames: int) -> bytes:
        """Run audio through a plugin (stub: returns the input unchanged).

        Args:
            plugin_id: a loaded plugin.
            audio_block: raw audio bytes.
            num_frames: number of frames in the block; negative values are rejected (the byte length is not cross-checked).

        Raises:
            SecurityError: plugin not loaded or num_frames negative.
        """
        if plugin_id not in self._loaded:
            raise SecurityError("Cannot process audio for a plugin that is not loaded.")
        if num_frames < 0:
            raise SecurityError("num_frames cannot be negative.")
        return audio_block
