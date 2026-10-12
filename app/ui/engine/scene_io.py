from __future__ import annotations

import json
from typing import Any

from app.security import MAX_PROJECT_FILE_BYTES, SecurityError, read_text_file_safely, write_text_file_safely
from app.ui.engine.viewport import MAX_SCENE_NODES, SceneGraph


SCENE_SUFFIX = ".ideas-scene"


def export_scene_file(path: str, state: dict[str, Any]) -> str:
    """Write inert Engine scene JSON to an explicit `.ideas-scene` path."""
    content = json.dumps(_sanitize_scene_state(state), indent=2, sort_keys=True)
    return write_text_file_safely(path, content + "\n", SCENE_SUFFIX, MAX_PROJECT_FILE_BYTES)


def import_scene_file(path: str) -> dict[str, Any]:
    """Read inert Engine scene JSON from an explicit `.ideas-scene` path."""
    if not path.endswith(SCENE_SUFFIX):
        raise SecurityError("Engine scene files must use the .ideas-scene suffix.")
    content = read_text_file_safely(path, max_bytes=MAX_PROJECT_FILE_BYTES)
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise SecurityError("Engine scene file is not valid JSON.") from exc
    return _sanitize_scene_state(data)


def _sanitize_scene_state(state: Any) -> dict[str, Any]:
    if not isinstance(state, dict):
        raise SecurityError("Engine scene root must be an object.")
    allowed = {"viewport", "shader_builder", "physics"}
    sanitized = {key: state[key] for key in allowed if key in state}
    viewport_state = sanitized.get("viewport")
    if viewport_state is not None:
        if not isinstance(viewport_state, dict):
            raise SecurityError("Engine scene viewport state must be an object.")
        graph_state = viewport_state.get("scene_graph", {"nodes": []})
        graph = SceneGraph()
        graph.load_state(graph_state)
        if graph.node_count() > MAX_SCENE_NODES:
            raise SecurityError("Engine scene has too many nodes.")
    shader_state = sanitized.get("shader_builder")
    if shader_state is not None and not isinstance(shader_state, dict):
        raise SecurityError("Engine scene shader state must be an object.")
    physics_state = sanitized.get("physics")
    if physics_state is not None and not isinstance(physics_state, dict):
        raise SecurityError("Engine scene physics state must be an object.")
    return sanitized
