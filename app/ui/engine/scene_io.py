# ==============================================================================
# Program: app/ui/engine/scene_io.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: reading and writing Engine scene files (*.ideas-scene). A scene file is plain JSON
# containing only data (scene graph, shader graph, physics settings): never code, so opening one
# cannot run anything. Because such a file may come from someone else, import re-validates the
# content through the real SceneGraph loader and keeps only known top-level sections.
# Used by: EngineWorkspace.export_scene / import_scene in app/ui/engine/workspace.py.
# Safety: file size capped at MAX_PROJECT_FILE_BYTES (2 MiB); suffix is enforced; node count and
# depth limits come from viewport.py (MAX_SCENE_NODES, MAX_SCENE_DEPTH) through SceneGraph.load_state.
# Tested by: tests/test_engine_integration.py (round trip, bad suffix, duplicate ids).
# ==============================================================================

from __future__ import annotations

import json
from typing import Any

from app.security import MAX_PROJECT_FILE_BYTES, SecurityError, read_text_file_safely, write_text_file_safely
from app.ui.engine.viewport import MAX_SCENE_NODES, SceneGraph


# Required file suffix; a dedicated suffix stops arbitrary .json files being treated as scenes.
SCENE_SUFFIX = ".ideas-scene"


def export_scene_file(path: str, state: dict[str, Any]) -> str:
    """Write inert Engine scene JSON to an explicit `.ideas-scene` path."""
    # Sanitise before writing so an invalid in-memory state can never produce a file that import would reject.
    # sort_keys + indent give stable, diff-friendly files.
    content = json.dumps(_sanitize_scene_state(state), indent=2, sort_keys=True)
    return write_text_file_safely(path, content + "\n", SCENE_SUFFIX, MAX_PROJECT_FILE_BYTES)


def import_scene_file(path: str) -> dict[str, Any]:
    """Read inert Engine scene JSON from an explicit `.ideas-scene` path."""
    # Check the suffix before touching the file system.
    if not path.endswith(SCENE_SUFFIX):
        raise SecurityError("Engine scene files must use the .ideas-scene suffix.")
    # The safe reader refuses binary data and files above the size cap.
    content = read_text_file_safely(path, max_bytes=MAX_PROJECT_FILE_BYTES)
    try:
        data = json.loads(content)
    # Convert to SecurityError so every failure mode of untrusted input surfaces as one exception type.
    except json.JSONDecodeError as exc:
        raise SecurityError("Engine scene file is not valid JSON.") from exc
    return _sanitize_scene_state(data)


def _sanitize_scene_state(state: Any) -> dict[str, Any]:
    """Return only the recognised, structurally valid sections of an untrusted scene state.

    Unknown top-level keys are dropped (so files cannot smuggle extra data into the workspace).
    The scene graph is fully loaded into a throwaway SceneGraph to trigger every validation rule
    (ids, shapes, numeric ranges, depth); the result is discarded, only the checks matter.
    """
    if not isinstance(state, dict):
        raise SecurityError("Engine scene root must be an object.")
    # Whitelist of sections the workspace knows how to restore.
    allowed = {"viewport", "shader_builder", "physics"}
    sanitized = {key: state[key] for key in allowed if key in state}
    viewport_state = sanitized.get("viewport")
    if viewport_state is not None:
        if not isinstance(viewport_state, dict):
            raise SecurityError("Engine scene viewport state must be an object.")
        # A missing scene graph is treated as an empty one.
        graph_state = viewport_state.get("scene_graph", {"nodes": []})
        graph = SceneGraph()
        # Raises SecurityError itself for duplicate ids, excessive depth and invalid node values.
        graph.load_state(graph_state)
        # Total node cap (2048) protects the renderer and physics from huge scenes.
        if graph.node_count() > MAX_SCENE_NODES:
            raise SecurityError("Engine scene has too many nodes.")
    # Shader and physics sections are validated by their own loaders later; here only the type is checked.
    shader_state = sanitized.get("shader_builder")
    if shader_state is not None and not isinstance(shader_state, dict):
        raise SecurityError("Engine scene shader state must be an object.")
    physics_state = sanitized.get("physics")
    if physics_state is not None and not isinstance(physics_state, dict):
        raise SecurityError("Engine scene physics state must be an object.")
    return sanitized
