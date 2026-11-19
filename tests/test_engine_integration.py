# ==============================================================================
# Program: tests/test_engine_integration.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: integration tests for the game-engine workspace (app/ui/engine): that its modules import,
# that EngineWorkspace builds and wires viewport, physics world and inspector together, and that
# scene import/export, undo/redo and size/format limits behave. Qt runs headless (offscreen).
# Run: python3 -m pytest tests/test_engine_integration.py -q     (needs PySide6)
# ==============================================================================

from __future__ import annotations

import os
from pathlib import Path

# Selects Qt's offscreen platform; must run before QApplication is created or Qt imports a display plugin.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from app.security import SecurityError


# Smoke test for circular imports and missing dependencies in the engine package. The imports are
# local so a failure shows up as this test; noqa silences the unused-import lint.
def test_engine_modules_import_cleanly() -> None:
    import app.ui.engine.ecs  # noqa: F401
    import app.ui.engine.physics  # noqa: F401
    import app.ui.engine.shader_builder  # noqa: F401
    import app.ui.engine.viewport  # noqa: F401
    import app.ui.engine.workspace  # noqa: F401


# The workspace builds with a default camera frame node in its scene graph and three tabs; `QApplication.instance() or QApplication([])` reuses an existing app.
def test_engine_workspace_instantiates() -> None:
    from app.ui.engine import EngineWorkspace

    app = QApplication.instance() or QApplication([])
    workspace = EngineWorkspace()

    assert app is not None
    assert workspace.viewport.scene_graph.find_node("camera_frame") is not None
    assert workspace.tabs.count() == 3


# Scenario: a rectangle high in the world, gravity 60, 0.5 s step. After stepping, the scene node's
# y must have increased (screen y grows downward), proving physics results are copied back to the
# scene graph that the viewport draws.
def test_engine_workspace_creates_objects_and_steps_physics() -> None:
    from app.ui.engine import EngineWorkspace

    QApplication.instance() or QApplication([])
    workspace = EngineWorkspace()
    node = workspace.create_scene_node("rectangle")
    node.x = 400.0
    node.y = 10.0
    # The physics world is built from the scene, so it must be rebuilt after hand-editing node fields.
    workspace._rebuild_world_from_scene()

    workspace.gravity_input.setValue(60.0)
    workspace.step_physics(0.5)

    assert workspace.viewport.scene_graph.find_node(node.node_id).y > 10.0


# Saving the workspace state and loading it into a fresh workspace must restore nodes and the
# selected node (this is what reopening a project does).
def test_engine_workspace_state_round_trip() -> None:
    from app.ui.engine import EngineWorkspace

    QApplication.instance() or QApplication([])
    workspace = EngineWorkspace()
    node = workspace.create_scene_node("circle")
    workspace.viewport.select_node(node.node_id)
    state = workspace.get_state()

    restored = EngineWorkspace()
    restored.load_state(state)

    assert restored.viewport.scene_graph.find_node(node.node_id) is not None
    assert restored.viewport.get_state()["selected_node_id"] == node.node_id


# One inspector update changes both scene-node fields (name, position) and the physics component
# (mass, velocity, restitution, layer), because the workspace keeps scene nodes and ECS bodies in sync.
def test_engine_workspace_inspector_update_and_physics_metadata() -> None:
    from app.ui.engine import EngineWorkspace
    from app.ui.engine.ecs import RigidBody

    QApplication.instance() or QApplication([])
    workspace = EngineWorkspace()
    node = workspace.create_scene_node("rectangle")

    # Returns True when something changed.
    assert workspace.update_selected_node(
        display_name="Crate",
        x=200.0,
        y=50.0,
        mass=5.0,
        velocity_x=12.0,
        collider_width=40.0,
        collider_height=30.0,
        is_static=False,
        restitution=0.8,
        friction=0.2,
        damping=0.1,
        gravity_scale=0.5,
        collision_layer=3,
    )

    edited = workspace.viewport.scene_graph.find_node(node.node_id)
    # Each node maps to an ECS entity id; the test reads the RigidBody to verify physics metadata.
    entity = workspace._node_entities[node.node_id]
    body = workspace.world.get_component(entity, RigidBody)
    assert edited.display_name == "Crate"
    assert edited.x == pytest.approx(200.0)
    assert body.mass == pytest.approx(5.0)
    assert body.velocity_x == pytest.approx(12.0)
    assert body.restitution == pytest.approx(0.8)
    assert body.collision_layer == 3


# History workflow: create then undo removes the node, redo restores it; duplicate and delete
# operate on the selected node and keep the scene graph consistent.
def test_engine_workspace_undo_redo_duplicate_delete() -> None:
    from app.ui.engine import EngineWorkspace

    QApplication.instance() or QApplication([])
    workspace = EngineWorkspace()
    node = workspace.create_scene_node("circle")
    assert workspace.viewport.scene_graph.find_node(node.node_id) is not None

    assert workspace.undo() is True
    assert workspace.viewport.scene_graph.find_node(node.node_id) is None
    assert workspace.redo() is True
    assert workspace.viewport.scene_graph.find_node(node.node_id) is not None

    duplicate = workspace.duplicate_selected_node()
    assert duplicate is not None
    assert workspace.viewport.scene_graph.find_node(duplicate.node_id) is not None
    assert workspace.delete_selected_node() is True
    assert workspace.viewport.scene_graph.find_node(duplicate.node_id) is None


# Export to a *.ideas-scene file and import into a different workspace; the node must reappear.
def test_engine_scene_import_export_round_trip(tmp_path: Path) -> None:
    from app.ui.engine import EngineWorkspace

    QApplication.instance() or QApplication([])
    workspace = EngineWorkspace()
    node = workspace.create_scene_node("rectangle")
    path = tmp_path / "level.ideas-scene"

    exported = workspace.export_scene(str(path))
    restored = EngineWorkspace()
    restored.import_scene(exported)

    assert restored.viewport.scene_graph.find_node(node.node_id) is not None


# Only the dedicated .ideas-scene suffix is accepted, so arbitrary JSON files are not parsed as scenes.
def test_engine_scene_import_rejects_bad_suffix(tmp_path: Path) -> None:
    from app.ui.engine.scene_io import import_scene_file

    bad = tmp_path / "level.json"
    bad.write_text("{}", encoding="utf-8")

    with pytest.raises(SecurityError):
        import_scene_file(str(bad))


# A scene file with two nodes sharing an id would corrupt lookups and physics mapping; it is rejected on import.
def test_engine_scene_import_rejects_duplicate_ids(tmp_path: Path) -> None:
    from app.ui.engine.scene_io import import_scene_file

    scene = tmp_path / "bad.ideas-scene"
    scene.write_text(
        '{"viewport":{"scene_graph":{"nodes":[{"node_id":"same"},{"node_id":"same"}]}}}',
        encoding="utf-8",
    )

    with pytest.raises(SecurityError, match="Duplicate"):
        import_scene_file(str(scene))


# Undo history stores serialised snapshots; a snapshot over max_state_bytes (32 here, 128 supplied)
# is refused so a huge scene cannot exhaust memory.
def test_engine_history_rejects_oversized_state() -> None:
    from app.ui.engine.history import EngineHistory

    history = EngineHistory(max_state_bytes=32)
    with pytest.raises(SecurityError):
        history.record({"large": "x" * 128})


# Regression guard: MainWindow must keep its build_engine_mode hook, or the Engine mode button would do nothing.
def test_application_module_wires_up_engine_builder() -> None:
    from app import application

    assert hasattr(application.MainWindow, "build_engine_mode")
