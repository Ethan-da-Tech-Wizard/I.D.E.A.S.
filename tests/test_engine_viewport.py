from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QApplication

from app.security import SecurityError
from app.ui.engine.viewport import MAX_POLYGON_POINTS, MAX_SCENE_DEPTH, SceneGraph, SceneNode, ViewportWidget


@pytest.fixture
def app() -> QApplication:
    existing = QApplication.instance()
    if existing is not None:
        return existing
    return QApplication([])


def test_scene_graph_add_find_and_remove_node() -> None:
    graph = SceneGraph()
    graph.add_node(SceneNode("root", children=[SceneNode("child", shape="circle")]))

    assert graph.find_node("root") is not None
    assert graph.find_node("child") is not None
    assert graph.remove_node("child") is True
    assert graph.find_node("child") is None
    assert graph.remove_node("missing") is False


def test_scene_graph_rejects_duplicate_node_ids() -> None:
    graph = SceneGraph()
    graph.add_node(SceneNode("node"))

    with pytest.raises(SecurityError):
        graph.add_node(SceneNode("node"))


def test_scene_node_validation() -> None:
    with pytest.raises(SecurityError):
        SceneNode("bad", shape="sprite")
    with pytest.raises(SecurityError):
        SceneNode("bad", width=-1)
    with pytest.raises(SecurityError):
        SceneNode("bad", color="not-a-color")
    with pytest.raises(SecurityError):
        SceneNode("bad", x=2_000_000)
    with pytest.raises(SecurityError):
        SceneNode("bad", restitution=2.0)
    with pytest.raises(SecurityError):
        SceneNode("bad", points=[(0.0, 0.0)] * (MAX_POLYGON_POINTS + 1))


def test_scene_graph_state_round_trip() -> None:
    graph = SceneGraph()
    graph.add_node(
        SceneNode(
            "poly",
            display_name="Polygon",
            shape="polygon",
            points=[(0, 0), (10, 0), (5, 5)],
            velocity_x=3.0,
            mass=2.0,
            is_trigger=True,
            restitution=0.75,
            collision_layer=2,
        )
    )

    restored = SceneGraph()
    restored.load_state(graph.to_dict())

    node = restored.find_node("poly")
    assert node is not None
    assert node.shape == "polygon"
    assert node.points == [(0.0, 0.0), (10.0, 0.0), (5.0, 5.0)]
    assert node.display_name == "Polygon"
    assert node.velocity_x == pytest.approx(3.0)
    assert node.mass == pytest.approx(2.0)
    assert node.is_trigger is True
    assert node.restitution == pytest.approx(0.75)
    assert node.collision_layer == 2


def test_viewport_pan_zoom_and_coordinate_round_trip(app: QApplication) -> None:
    viewport = ViewportWidget()
    viewport.resize(400, 300)
    viewport.set_view(10, 20, 2.0)
    viewport.pan_by(5, -10)

    screen = viewport.world_to_screen(QPointF(30, 40))
    assert screen == QPointF(75, 90)
    assert viewport.screen_to_world(screen) == QPointF(30, 40)

    before = viewport.screen_to_world(QPointF(100, 100))
    viewport.zoom_by(2.0, QPointF(100, 100))
    after = viewport.screen_to_world(QPointF(100, 100))
    assert after == before


def test_viewport_state_round_trip(app: QApplication) -> None:
    viewport = ViewportWidget()
    viewport.set_view(12, 34, 1.5)
    viewport.add_node(SceneNode("rect", x=1, y=2, color="#00FF00"))

    restored = ViewportWidget()
    restored.load_state(viewport.get_state())

    state = restored.get_state()
    assert state["view_offset"] == {"x": 12.0, "y": 34.0}
    assert state["zoom_level"] == pytest.approx(1.5)
    assert restored.scene_graph.find_node("rect") is not None


def test_viewport_clamps_zoom(app: QApplication) -> None:
    viewport = ViewportWidget()
    viewport.set_view(0, 0, 100.0)
    assert viewport.get_state()["zoom_level"] == pytest.approx(8.0)
    viewport.set_view(0, 0, 0.001)
    assert viewport.get_state()["zoom_level"] == pytest.approx(0.1)


def test_viewport_selection_hit_test_and_movement(app: QApplication) -> None:
    viewport = ViewportWidget()
    viewport.add_node(SceneNode("back", x=0, y=0, width=100, height=100))
    viewport.add_node(SceneNode("front", x=10, y=10, width=20, height=20))

    hit = viewport.hit_test(QPointF(15, 15))
    assert hit is not None
    assert hit.node_id == "front"

    viewport.select_node("front")
    assert viewport.move_selected_by(5, 10) is True
    assert viewport.scene_graph.find_node("front").x == pytest.approx(15.0)
    assert viewport.scene_graph.find_node("front").y == pytest.approx(20.0)


def test_parent_movement_moves_children(app: QApplication) -> None:
    viewport = ViewportWidget()
    child = SceneNode("child", x=10, y=10)
    parent = SceneNode("parent", x=0, y=0, children=[child])
    viewport.add_node(parent)
    viewport.select_node("parent")

    viewport.move_selected_by(5, 7)

    assert parent.x == pytest.approx(5)
    assert parent.y == pytest.approx(7)
    assert child.x == pytest.approx(15)
    assert child.y == pytest.approx(17)


def test_duplicate_and_delete_selected(app: QApplication) -> None:
    viewport = ViewportWidget()
    viewport.add_node(SceneNode("node", children=[SceneNode("child")]))
    viewport.select_node("node")

    duplicate = viewport.duplicate_selected()

    assert duplicate is not None
    assert duplicate.node_id == "node_copy"
    assert duplicate.children[0].node_id == "child_copy"
    assert viewport.delete_selected() is True
    assert viewport.scene_graph.find_node("node_copy") is None


def test_scene_graph_load_rejects_duplicate_child_ids() -> None:
    graph = SceneGraph()
    with pytest.raises(SecurityError, match="Duplicate"):
        graph.load_state(
            {
                "nodes": [
                    {
                        "node_id": "root",
                        "children": [
                            {"node_id": "dupe"},
                            {"node_id": "dupe"},
                        ],
                    }
                ]
            }
        )


def test_scene_graph_load_rejects_excessive_depth() -> None:
    state = {"node_id": "root"}
    current = state
    for index in range(MAX_SCENE_DEPTH + 1):
        child = {"node_id": f"child_{index}"}
        current["children"] = [child]
        current = child

    graph = SceneGraph()
    with pytest.raises(SecurityError, match="too deep"):
        graph.load_state({"nodes": [state]})


def test_viewport_grid_snap_state_round_trip(app: QApplication) -> None:
    viewport = ViewportWidget()
    viewport.add_node(SceneNode("node", x=3, y=4))
    viewport.select_node("node")
    viewport.set_grid(True, 10.0, True)
    viewport.move_selected_by(3, 3)

    assert viewport.scene_graph.find_node("node").x == pytest.approx(10.0)
    assert viewport.scene_graph.find_node("node").y == pytest.approx(10.0)

    restored = ViewportWidget()
    restored.load_state(viewport.get_state())
    assert restored.get_state()["snap_to_grid"] is True
    assert restored.get_state()["grid_size"] == pytest.approx(10.0)
    assert restored.get_state()["selected_node_id"] == "node"


def test_viewport_reset_camera_and_zoom_to_fit(app: QApplication) -> None:
    viewport = ViewportWidget()
    viewport.resize(300, 200)
    viewport.add_node(SceneNode("node", x=100, y=100, width=50, height=50))
    viewport.zoom_to_fit()
    assert viewport.get_state()["zoom_level"] > 1.0
    viewport.reset_camera()
    assert viewport.get_state()["view_offset"] == {"x": 0.0, "y": 0.0}
    assert viewport.get_state()["zoom_level"] == pytest.approx(1.0)
