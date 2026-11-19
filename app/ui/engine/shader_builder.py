# ==============================================================================
# Program: app/ui/engine/shader_builder.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: a visual node editor for shader-like graphs in the Engine workspace. The user adds nodes
# (Color, Blend, Noise, Transform, Output) from a toolbar, drags them around and joins output ports
# to input ports. The graph can be saved as JSON in the project and exported as a text DESCRIPTOR.
# Important design point: the descriptor is inert text. Nothing in the app compiles or runs it, so
# a shared project cannot execute code through the shader graph (see to_shader_descriptor).
# Classes: ShaderNode (a draggable QGraphicsItem), ShaderConnection (a bezier curve item),
# ShaderGraph (the QGraphicsScene holding both plus the validation rules), ShaderBuilderWidget
# (toolbar + view; the part workspace.py embeds in a tab).
# Used by: app/ui/engine/workspace.py (state saved under the "shader_builder" key of a scene);
# tests/test_shader_builder.py.
# Known limits / bug noticed: connection curves are only recalculated by update_path(); nothing
# calls it when a node is dragged, so existing connections do not follow moved nodes until the
# graph is reloaded. Parameters are free-form dicts and are not validated per node type.
# ==============================================================================

from __future__ import annotations

import json
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsPathItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QPushButton,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from app.security import SecurityError


# Whitelist of node kinds; loading a file with any other type raises SecurityError.
NODE_TYPES = {"Color", "Blend", "Noise", "Transform", "Output"}
# Every node has exactly one input port (left) and one output port (right); connections run output -> input only.
OUTPUT_PORT = "output"
INPUT_PORT = "input"


class ShaderNode(QGraphicsItem):
    """Draggable shader operation node with input and output ports."""

    WIDTH = 140.0
    HEIGHT = 82.0
    PORT_RADIUS = 6.0

    def __init__(
        self,
        node_id: int,
        node_type: str,
        parameters: dict[str, Any] | None = None,
        x: float = 0.0,
        y: float = 0.0,
    ) -> None:
        """Create a node at (x, y).

        Args:
            node_id: unique integer id within the graph (used in saved connections).
            node_type: one of NODE_TYPES.
            parameters: free-form settings dict (for example {"r": 255}); stored as given.
            x, y: scene position.

        Raises:
            SecurityError: unknown node type.
        """
        super().__init__()
        if node_type not in NODE_TYPES:
            raise SecurityError(f"Unknown shader node type: {node_type}")
        self.node_id = node_id
        self.node_type = node_type
        # A fresh dict when none is given, so nodes never share one mutable default.
        self.parameters = parameters or {}
        self.setPos(x, y)
        # Movable and selectable by the user; ItemSendsGeometryChanges is set so position changes could notify the scene (currently nothing listens, see the known limit in the file header).
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )

    # Return the node bounds for Qt painting.
    #
    # Qt requires this to cover everything paint() draws; 140 x 82 local units, origin at the top-left.
    def boundingRect(self) -> QRectF:
        """Return the node bounds for Qt painting."""
        return QRectF(0.0, 0.0, self.WIDTH, self.HEIGHT)

    # Paint the node body and ports.
    #
    # The border turns blue (#007acc) when selected. Input port is green on the left edge, output port is pink on the right edge.
    def paint(self, painter: QPainter, _option: Any, _widget: QWidget | None = None) -> None:
        """Paint the node body and ports."""
        # Smooth edges for the rounded rectangle and port circles.
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor("#007acc" if self.isSelected() else "#3e3e42"), 2.0))
        painter.setBrush(QBrush(QColor("#252526")))
        painter.drawRoundedRect(self.boundingRect(), 5.0, 5.0)
        painter.setPen(QColor("#ffffff"))
        # Title text is the node type, drawn inside the top-left of the box.
        painter.drawText(QRectF(12.0, 8.0, self.WIDTH - 24.0, 22.0), Qt.AlignmentFlag.AlignLeft, self.node_type)
        painter.setBrush(QBrush(QColor("#6a9955")))
        painter.drawEllipse(self.port_position(INPUT_PORT), self.PORT_RADIUS, self.PORT_RADIUS)
        painter.setBrush(QBrush(QColor("#c586c0")))
        painter.drawEllipse(self.port_position(OUTPUT_PORT), self.PORT_RADIUS, self.PORT_RADIUS)

    # Return a local-space port position.
    #
    # Ports sit on the vertical middle of the left (input) and right (output) edges.
    #
    # Raises:
    #     SecurityError: unknown port name.
    def port_position(self, port_name: str) -> QPointF:
        """Return a local-space port position."""
        if port_name == INPUT_PORT:
            return QPointF(0.0, self.HEIGHT / 2.0)
        if port_name == OUTPUT_PORT:
            return QPointF(self.WIDTH, self.HEIGHT / 2.0)
        raise SecurityError(f"Unknown port: {port_name}")

    # Return a scene-space port position.
    #
    # Converts the local position with mapToScene so it stays correct after the node is moved.
    def scene_port_position(self, port_name: str) -> QPointF:
        """Return a scene-space port position."""
        return self.mapToScene(self.port_position(port_name))

    # Serialize the node descriptor (id, type, params, scene x/y) for JSON.
    def to_dict(self) -> dict[str, Any]:
        """Serialize the node descriptor."""
        return {
            "id": self.node_id,
            "type": self.node_type,
            "params": self.parameters,
            "x": self.pos().x(),
            "y": self.pos().y(),
        }


class ShaderConnection(QGraphicsPathItem):
    """Bezier path connecting a node output port to a node input port."""

    def __init__(self, from_node: ShaderNode, from_port: str, to_node: ShaderNode, to_port: str) -> None:
        """Create a connection item between two ports.

        Args:
            from_node, from_port: source node and its port name (must be "output").
            to_node, to_port: target node and its port name (must be "input").
        """
        super().__init__()
        self.from_node = from_node
        self.from_port = from_port
        self.to_node = to_node
        self.to_port = to_port
        # Gold 2 px curve.
        self.setPen(QPen(QColor("#d7ba7d"), 2.0))
        self.update_path()

    # Recalculate the connection path from node port positions.
    #
    # A cubic bezier leaves the start horizontally to the right and enters the end from the left.
    def update_path(self) -> None:
        """Recalculate the connection path from node port positions."""
        start = self.from_node.scene_port_position(self.from_port)
        end = self.to_node.scene_port_position(self.to_port)
        # Control-point offset: half the horizontal distance, at least 40 px, so the curve still bows out nicely when nodes are close or the target is behind the source.
        dx = max(abs(end.x() - start.x()) * 0.5, 40.0)
        path = QPainterPath(start)
        path.cubicTo(QPointF(start.x() + dx, start.y()), QPointF(end.x() - dx, end.y()), end)
        self.setPath(path)

    # Serialize the connection descriptor (node ids and port names).
    def to_dict(self) -> dict[str, Any]:
        """Serialize the connection descriptor."""
        return {
            "from_node": self.from_node.node_id,
            "from_port": self.from_port,
            "to_node": self.to_node.node_id,
            "to_port": self.to_port,
        }


class ShaderGraph(QGraphicsScene):
    """Graphics scene containing shader nodes and connections."""

    def __init__(self, parent: QWidget | None = None) -> None:
        """Create an empty graph.

        Args:
            parent: optional Qt parent (the widget that owns it).
        """
        super().__init__(parent)
        # _nodes: id -> node; _connections: list in creation order; _next_node_id: next automatically assigned id.
        self._nodes: dict[int, ShaderNode] = {}
        self._connections: list[ShaderConnection] = []
        self._next_node_id = 0

    # Return a copy of the id -> node mapping (copy so callers cannot change the graph by accident).
    @property
    def nodes(self) -> dict[int, ShaderNode]:
        """Return node mapping."""
        return dict(self._nodes)

    # Return the connections as a read-only tuple.
    @property
    def connections(self) -> tuple[ShaderConnection, ...]:
        """Return active connections."""
        return tuple(self._connections)

    # Create a node and add it to the scene.
    #
    # Args:
    #     node_type: one of NODE_TYPES.
    #     parameters: optional settings dict.
    #     x, y: scene position.
    #     node_id: explicit id (used when loading a file); None assigns the next free id.
    #
    # Raises:
    #     SecurityError: duplicate id or unknown node type.
    def add_node(
        self,
        node_type: str,
        parameters: dict[str, Any] | None = None,
        x: float = 0.0,
        y: float = 0.0,
        node_id: int | None = None,
    ) -> ShaderNode:
        """Create and add a shader node."""
        resolved_id = self._next_node_id if node_id is None else node_id
        if resolved_id in self._nodes:
            raise SecurityError(f"Duplicate shader node id: {resolved_id}")
        node = ShaderNode(resolved_id, node_type, parameters, x, y)
        self.addItem(node)
        self._nodes[resolved_id] = node
        # Keep the counter above every id in use, so automatically assigned ids never collide after loading explicit ids.
        self._next_node_id = max(self._next_node_id, resolved_id + 1)
        return node

    # Connect an output port to an input port and return the new connection item.
    #
    # Raises:
    #     SecurityError: wrong port direction, self-connection, unknown node or duplicate connection.
    def connect(self, from_node_id: int, from_port: str, to_node_id: int, to_port: str) -> ShaderConnection:
        """Connect an output port to an input port."""
        # Direction rule: signals flow from an output to an input.
        if from_port != OUTPUT_PORT or to_port != INPUT_PORT:
            raise SecurityError("Shader connections must go output to input.")
        # A node feeding itself would be a trivial cycle. Longer cycles between several nodes are NOT detected.
        if from_node_id == to_node_id:
            raise SecurityError("Shader node cannot connect to itself.")
        try:
            from_node = self._nodes[from_node_id]
            to_node = self._nodes[to_node_id]
        except KeyError as exc:
            raise SecurityError("Shader connection references an unknown node.") from exc
        # Linear duplicate check; graphs are small.
        for connection in self._connections:
            if (
                connection.from_node.node_id == from_node_id
                and connection.from_port == from_port
                and connection.to_node.node_id == to_node_id
                and connection.to_port == to_port
            ):
                raise SecurityError("Duplicate shader connection.")
        connection = ShaderConnection(from_node, from_port, to_node, to_port)
        self.addItem(connection)
        self._connections.append(connection)
        return connection

    # Remove a node and every connection attached to it. Returns False for an unknown id.
    def remove_node(self, node_id: int) -> bool:
        """Remove a node and all cascading connections."""
        node = self._nodes.pop(node_id, None)
        if node is None:
            return False
        # Iterate over a copy because the list is modified inside the loop.
        for connection in list(self._connections):
            if connection.from_node.node_id == node_id or connection.to_node.node_id == node_id:
                self._connections.remove(connection)
                self.removeItem(connection)
        self.removeItem(node)
        return True

    # Serialize the graph as JSON with nodes sorted by id and sorted keys, so equal graphs give identical text (diff-friendly, test-friendly).
    def to_json(self) -> str:
        """Serialize the graph descriptor as stable JSON."""
        payload = {
            "nodes": [self._nodes[node_id].to_dict() for node_id in sorted(self._nodes)],
            "connections": [connection.to_dict() for connection in self._connections],
        }
        return json.dumps(payload, sort_keys=True)

    # Generate an inert shader descriptor text from the graph.
    #
    # Format: a banner, then `shader_graph { node ID TYPE params=JSON ... connect A.output -> B.input ... }`. It is data for humans and future tools; nothing here executes it.
    def to_shader_descriptor(self) -> str:
        """Generate an inert shader descriptor text from the graph."""
        # Reuse the JSON form so the descriptor and the saved file cannot disagree.
        payload = json.loads(self.to_json())
        lines = [
            "// I.D.E.A.S. shader descriptor",
            "// This text is not compiled or executed by the Engine workbench.",
            "shader_graph {",
        ]
        for node in payload["nodes"]:
            params = json.dumps(node["params"], sort_keys=True)
            lines.append(f"  node {node['id']} {node['type']} params={params}")
        for connection in payload["connections"]:
            lines.append(
                "  connect "
                f"{connection['from_node']}.{connection['from_port']} -> "
                f"{connection['to_node']}.{connection['to_port']}"
            )
        lines.append("}")
        return "\n".join(lines) + "\n"

    # Replace the graph with the one described by `content`.
    #
    # Args:
    #     content: JSON text produced by to_json.
    #
    # Raises:
    #     SecurityError: root is not an object, a node/connection is invalid (unknown type, duplicate id, bad ports).
    #     json.JSONDecodeError / KeyError / ValueError: malformed JSON or missing/garbled fields are not converted.
    def load_json(self, content: str) -> None:
        """Restore graph nodes and connections from a JSON descriptor."""
        data = json.loads(content)
        if not isinstance(data, dict):
            raise SecurityError("Shader graph JSON must be an object.")
        # QGraphicsScene.clear() deletes all items; the dictionaries are reset to match.
        self.clear()
        self._nodes.clear()
        self._connections.clear()
        self._next_node_id = 0
        # Nodes first, connections second: connect() needs both endpoints to exist.
        for raw_node in data.get("nodes", []):
            self.add_node(
                str(raw_node["type"]),
                dict(raw_node.get("params", {})),
                float(raw_node.get("x", 0.0)),
                float(raw_node.get("y", 0.0)),
                int(raw_node["id"]),
            )
        for raw_connection in data.get("connections", []):
            self.connect(
                int(raw_connection["from_node"]),
                str(raw_connection["from_port"]),
                int(raw_connection["to_node"]),
                str(raw_connection["to_port"]),
            )


class ShaderBuilderWidget(QWidget):
    """Toolbar and graphics view wrapper for shader graph editing."""

    def __init__(self, parent: QWidget | None = None) -> None:
        """Create the widget with an empty graph and its view.

        Args:
            parent: optional Qt parent.
        """
        super().__init__(parent)
        # Name used by stylesheets and tests to find this widget.
        self.setObjectName("ShaderBuilderWidget")
        # Parent is self so the scene is destroyed with the widget.
        self.graph = ShaderGraph(self)
        self.view = QGraphicsView(self.graph)
        self.setup_ui()

    # Build the toolbar and graph viewport.
    def setup_ui(self) -> None:
        """Build the toolbar and graph viewport."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        toolbar = QToolBar(self)
        for node_type in sorted(NODE_TYPES):
            action = toolbar.addAction(node_type)
            # Default argument kind=node_type binds this loop iteration's value; nodes are added at (40, 40).
            action.triggered.connect(lambda _checked=False, kind=node_type: self.graph.add_node(kind, x=40.0, y=40.0))
        layout.addWidget(toolbar)
        layout.addWidget(self.view)

    # Return graph state for project persistence (a plain dict).
    def get_state(self) -> dict[str, Any]:
        """Return graph state for project persistence."""
        return json.loads(self.graph.to_json())

    # Restore graph state from project persistence.
    def load_state(self, state: dict[str, Any]) -> None:
        """Restore graph state from project persistence."""
        self.graph.load_json(json.dumps(state))

    # Return inert shader descriptor text for the current graph.
    def shader_descriptor(self) -> str:
        """Return inert shader descriptor text for the current graph."""
        return self.graph.to_shader_descriptor()
