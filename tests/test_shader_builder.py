from __future__ import annotations

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from app.security import SecurityError
from app.ui.engine.shader_builder import ShaderBuilderWidget, ShaderGraph


@pytest.fixture
def app() -> QApplication:
    existing = QApplication.instance()
    if existing is not None:
        return existing
    return QApplication([])


def test_node_creation(app: QApplication) -> None:
    graph = ShaderGraph()
    node = graph.add_node("Color", {"r": 255, "g": 0, "b": 128}, x=100, y=200)

    assert node.node_id == 0
    assert node.node_type == "Color"
    assert node.to_dict()["params"]["r"] == 255


def test_connection_validation_rejects_invalid_ports(app: QApplication) -> None:
    graph = ShaderGraph()
    first = graph.add_node("Color")
    second = graph.add_node("Output")

    with pytest.raises(SecurityError):
        graph.connect(first.node_id, "output", second.node_id, "output")

    with pytest.raises(SecurityError):
        graph.connect(first.node_id, "input", second.node_id, "input")


def test_connection_creation_and_duplicate_rejection(app: QApplication) -> None:
    graph = ShaderGraph()
    first = graph.add_node("Color")
    second = graph.add_node("Output")

    graph.connect(first.node_id, "output", second.node_id, "input")

    assert len(graph.connections) == 1
    with pytest.raises(SecurityError):
        graph.connect(first.node_id, "output", second.node_id, "input")


def test_json_serialization_round_trip(app: QApplication) -> None:
    graph = ShaderGraph()
    color = graph.add_node("Color", {"r": 255}, x=100, y=200)
    output = graph.add_node("Output", {}, x=320, y=200)
    graph.connect(color.node_id, "output", output.node_id, "input")

    restored = ShaderGraph()
    restored.load_json(graph.to_json())
    payload = json.loads(restored.to_json())

    assert payload["nodes"][0]["type"] == "Color"
    assert payload["nodes"][0]["x"] == pytest.approx(100.0)
    assert payload["connections"] == [
        {"from_node": 0, "from_port": "output", "to_node": 1, "to_port": "input"}
    ]


def test_node_deletion_cascades_connections(app: QApplication) -> None:
    graph = ShaderGraph()
    color = graph.add_node("Color")
    blend = graph.add_node("Blend")
    output = graph.add_node("Output")
    graph.connect(color.node_id, "output", blend.node_id, "input")
    graph.connect(blend.node_id, "output", output.node_id, "input")

    assert graph.remove_node(blend.node_id) is True

    assert len(graph.connections) == 0
    assert graph.remove_node(999) is False


def test_shader_builder_widget_state_round_trip(app: QApplication) -> None:
    widget = ShaderBuilderWidget()
    color = widget.graph.add_node("Color")
    output = widget.graph.add_node("Output")
    widget.graph.connect(color.node_id, "output", output.node_id, "input")

    restored = ShaderBuilderWidget()
    restored.load_state(widget.get_state())

    assert len(restored.graph.nodes) == 2
    assert len(restored.graph.connections) == 1


def test_shader_descriptor_is_inert_text(app: QApplication) -> None:
    graph = ShaderGraph()
    color = graph.add_node("Color", {"r": 255})
    output = graph.add_node("Output")
    graph.connect(color.node_id, "output", output.node_id, "input")

    descriptor = graph.to_shader_descriptor()

    assert descriptor.startswith("// I.D.E.A.S. shader descriptor")
    assert "node 0 Color" in descriptor
    assert "connect 0.output -> 1.input" in descriptor
