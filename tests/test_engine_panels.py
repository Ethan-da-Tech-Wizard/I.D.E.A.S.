from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from app.ui.engine import EnginePropertiesPanel, EngineSceneTreePanel, EngineWorkspace
from app.ui.engine.viewport import SceneNode


def ensure_app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_scene_tree_reflects_live_hierarchy_and_selects_viewport_node() -> None:
    ensure_app()
    workspace = EngineWorkspace()
    parent = SceneNode("parent", display_name="Parent", children=[SceneNode("child", display_name="Child")])
    workspace.viewport.add_node(parent)
    panel = EngineSceneTreePanel(workspace)

    panel.refresh()
    child_item = panel.findItems("Child", Qt.MatchFlag.MatchRecursive | Qt.MatchFlag.MatchExactly)[0]
    panel.setCurrentItem(child_item)

    assert workspace.viewport.selected_node_id == "child"


def test_scene_tree_follows_viewport_selection() -> None:
    ensure_app()
    workspace = EngineWorkspace()
    node = workspace.create_scene_node("rectangle")
    panel = EngineSceneTreePanel(workspace)

    workspace.viewport.select_node(node.node_id)

    assert panel.currentItem().data(0, Qt.ItemDataRole.UserRole) == node.node_id


def test_scene_tree_refreshes_when_workspace_changes() -> None:
    ensure_app()
    workspace = EngineWorkspace()
    panel = EngineSceneTreePanel(workspace)
    before = panel.topLevelItemCount()

    workspace.create_scene_node("circle")

    assert panel.topLevelItemCount() == before + 1


def test_properties_panel_applies_edits_through_workspace() -> None:
    ensure_app()
    workspace = EngineWorkspace()
    node = workspace.create_scene_node("rectangle")
    panel = EnginePropertiesPanel(workspace)

    panel.name_input.setText("Edited Crate")
    panel.x_input.setValue(321.0)
    panel.mass_input.setValue(7.0)
    panel.apply_changes()

    edited = workspace.viewport.scene_graph.find_node(node.node_id)
    assert edited.display_name == "Edited Crate"
    assert edited.x == 321.0
    assert edited.mass == 7.0


def test_workspace_exposes_live_dock_panel_factories() -> None:
    ensure_app()
    workspace = EngineWorkspace()

    factories = workspace.dock_panel_factories()

    assert sorted(factories) == ["Console", "Properties", "Scene Tree"]
    assert isinstance(factories["Scene Tree"](), EngineSceneTreePanel)
    assert isinstance(factories["Properties"](), EnginePropertiesPanel)
