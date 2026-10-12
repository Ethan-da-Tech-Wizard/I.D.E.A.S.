from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPaintEvent, QPen, QPolygonF, QTransform, QWheelEvent
from PySide6.QtWidgets import QWidget

from app.security import SecurityError


ALLOWED_SHAPES = {"rectangle", "circle", "line", "polygon"}
MIN_ZOOM = 0.1
MAX_ZOOM = 8.0
MAX_SCENE_NODES = 2048
MAX_SCENE_DEPTH = 16
MAX_POLYGON_POINTS = 256
MAX_NODE_ID_LENGTH = 96
MAX_DISPLAY_NAME_LENGTH = 160
MAX_ABS_COORDINATE = 1_000_000.0
MAX_NODE_SIZE = 100_000.0
MAX_VELOCITY = 100_000.0
MAX_MASS = 1_000_000.0


@dataclass
class SceneNode:
    """A serializable primitive node in the engine scene graph."""

    node_id: str
    display_name: str | None = None
    x: float = 0.0
    y: float = 0.0
    width: float = 50.0
    height: float = 50.0
    color: str = "#FFFFFF"
    shape: str = "rectangle"
    velocity_x: float = 0.0
    velocity_y: float = 0.0
    mass: float = 1.0
    is_static: bool = False
    collider_width: float | None = None
    collider_height: float | None = None
    is_trigger: bool = False
    restitution: float = 0.25
    friction: float = 0.0
    damping: float = 0.0
    gravity_scale: float = 1.0
    collision_layer: int = 0
    points: list[tuple[float, float]] = field(default_factory=list)
    children: list["SceneNode"] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.node_id or len(self.node_id) > MAX_NODE_ID_LENGTH:
            raise SecurityError("Scene node id must not be empty.")
        if self.display_name is not None and len(self.display_name) > MAX_DISPLAY_NAME_LENGTH:
            raise SecurityError("Scene node display name is too long.")
        if self.shape not in ALLOWED_SHAPES:
            raise SecurityError(f"Unsupported scene node shape: {self.shape}")
        if abs(self.x) > MAX_ABS_COORDINATE or abs(self.y) > MAX_ABS_COORDINATE:
            raise SecurityError("Scene node coordinates are out of bounds.")
        if self.width < 0 or self.height < 0 or self.width > MAX_NODE_SIZE or self.height > MAX_NODE_SIZE:
            raise SecurityError("Scene node dimensions must not be negative.")
        if not QColor(self.color).isValid():
            raise SecurityError("Scene node color must be a valid color.")
        if abs(self.velocity_x) > MAX_VELOCITY or abs(self.velocity_y) > MAX_VELOCITY:
            raise SecurityError("Scene node velocity is out of bounds.")
        if self.mass <= 0 or self.mass > MAX_MASS:
            raise SecurityError("Scene node mass must be positive.")
        if self.collider_width is not None and (self.collider_width < 0 or self.collider_width > MAX_NODE_SIZE):
            raise SecurityError("Collider width must not be negative.")
        if self.collider_height is not None and (self.collider_height < 0 or self.collider_height > MAX_NODE_SIZE):
            raise SecurityError("Collider height must not be negative.")
        if not 0.0 <= self.restitution <= 1.0:
            raise SecurityError("Restitution must be between 0 and 1.")
        if not 0.0 <= self.friction <= 1.0:
            raise SecurityError("Friction must be between 0 and 1.")
        if not 0.0 <= self.damping <= 10.0:
            raise SecurityError("Damping must be between 0 and 10.")
        if not -10.0 <= self.gravity_scale <= 10.0:
            raise SecurityError("Gravity scale is out of bounds.")
        if self.collision_layer < 0 or self.collision_layer > 64:
            raise SecurityError("Collision layer must not be negative.")
        if len(self.points) > MAX_POLYGON_POINTS:
            raise SecurityError("Scene node has too many polygon points.")
        for point_x, point_y in self.points:
            if abs(point_x) > MAX_ABS_COORDINATE or abs(point_y) > MAX_ABS_COORDINATE:
                raise SecurityError("Scene node polygon point is out of bounds.")

    def to_dict(self) -> dict[str, Any]:
        """Serialize this node and all children."""
        return {
            "node_id": self.node_id,
            "display_name": self.display_name or self.node_id,
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
            "color": self.color,
            "shape": self.shape,
            "velocity_x": self.velocity_x,
            "velocity_y": self.velocity_y,
            "mass": self.mass,
            "is_static": self.is_static,
            "collider_width": self.collider_width,
            "collider_height": self.collider_height,
            "is_trigger": self.is_trigger,
            "restitution": self.restitution,
            "friction": self.friction,
            "damping": self.damping,
            "gravity_scale": self.gravity_scale,
            "collision_layer": self.collision_layer,
            "points": [[x, y] for x, y in self.points],
            "children": [child.to_dict() for child in self.children],
        }

    @classmethod
    def from_dict(cls, state: dict[str, Any]) -> "SceneNode":
        """Restore a scene node from serialized state."""
        if not isinstance(state, dict):
            raise SecurityError("Scene node state must be an object.")
        raw_points = state.get("points", [])
        if not isinstance(raw_points, list):
            raise SecurityError("Scene node points must be a list.")
        if len(raw_points) > MAX_POLYGON_POINTS:
            raise SecurityError("Scene node has too many polygon points.")
        points = [(float(point[0]), float(point[1])) for point in raw_points]
        raw_children = state.get("children", [])
        if not isinstance(raw_children, list):
            raise SecurityError("Scene node children must be a list.")
        children = [cls.from_dict(child) for child in raw_children]
        return cls(
            node_id=str(state.get("node_id", "")),
            display_name=str(state.get("display_name")) if state.get("display_name") is not None else None,
            x=float(state.get("x", 0.0)),
            y=float(state.get("y", 0.0)),
            width=float(state.get("width", 50.0)),
            height=float(state.get("height", 50.0)),
            color=str(state.get("color", "#FFFFFF")),
            shape=str(state.get("shape", "rectangle")),
            velocity_x=float(state.get("velocity_x", 0.0)),
            velocity_y=float(state.get("velocity_y", 0.0)),
            mass=float(state.get("mass", 1.0)),
            is_static=bool(state.get("is_static", False)),
            collider_width=float(state["collider_width"]) if state.get("collider_width") is not None else None,
            collider_height=float(state["collider_height"]) if state.get("collider_height") is not None else None,
            is_trigger=bool(state.get("is_trigger", False)),
            restitution=float(state.get("restitution", 0.25)),
            friction=float(state.get("friction", 0.0)),
            damping=float(state.get("damping", 0.0)),
            gravity_scale=float(state.get("gravity_scale", 1.0)),
            collision_layer=int(state.get("collision_layer", 0)),
            points=points,
            children=children,
        )


class SceneGraph:
    """Stores and serializes top-level scene nodes."""

    def __init__(self) -> None:
        self._nodes: list[SceneNode] = []

    @property
    def nodes(self) -> tuple[SceneNode, ...]:
        """Return immutable access to top-level nodes."""
        return tuple(self._nodes)

    def add_node(self, node: SceneNode) -> None:
        """Add a top-level node to the graph."""
        if self.find_node(node.node_id) is not None:
            raise SecurityError(f"Duplicate scene node id: {node.node_id}")
        self._validate_tree(node, seen={existing.node_id for existing in self._nodes}, depth=1)
        self._nodes.append(node)

    def remove_node(self, node_id: str) -> bool:
        """Remove a top-level or child node by id."""
        for index, node in enumerate(self._nodes):
            if node.node_id == node_id:
                del self._nodes[index]
                return True
            if self._remove_child(node, node_id):
                return True
        return False

    def find_node(self, node_id: str) -> SceneNode | None:
        """Find a node by id anywhere in the graph."""
        for node in self._nodes:
            found = self._find_in_tree(node, node_id)
            if found is not None:
                return found
        return None

    def to_dict(self) -> dict[str, Any]:
        """Serialize the scene graph."""
        return {"nodes": [node.to_dict() for node in self._nodes]}

    def load_state(self, state: dict[str, Any]) -> None:
        """Restore the graph from serialized state."""
        if not isinstance(state, dict):
            raise SecurityError("Scene graph state must be an object.")
        raw_nodes = state.get("nodes", [])
        if not isinstance(raw_nodes, list):
            raise SecurityError("Scene graph nodes must be a list.")
        nodes = [SceneNode.from_dict(node_state) for node_state in raw_nodes]
        seen: set[str] = set()
        total = 0
        for node in nodes:
            total += self._validate_tree(node, seen=seen, depth=1)
        if total > MAX_SCENE_NODES:
            raise SecurityError("Scene graph has too many nodes.")
        self._nodes = nodes

    def node_count(self) -> int:
        """Return the total number of nodes in the graph."""
        return sum(self._count_tree(node) for node in self._nodes)

    def _find_in_tree(self, node: SceneNode, node_id: str) -> SceneNode | None:
        if node.node_id == node_id:
            return node
        for child in node.children:
            found = self._find_in_tree(child, node_id)
            if found is not None:
                return found
        return None

    def _remove_child(self, node: SceneNode, node_id: str) -> bool:
        for index, child in enumerate(node.children):
            if child.node_id == node_id:
                del node.children[index]
                return True
            if self._remove_child(child, node_id):
                return True
        return False

    def _validate_tree(self, node: SceneNode, seen: set[str], depth: int) -> int:
        if depth > MAX_SCENE_DEPTH:
            raise SecurityError("Scene graph hierarchy is too deep.")
        node.__post_init__()
        if node.node_id in seen:
            raise SecurityError(f"Duplicate scene node id: {node.node_id}")
        seen.add(node.node_id)
        total = 1
        for child in node.children:
            total += self._validate_tree(child, seen, depth + 1)
        if total > MAX_SCENE_NODES:
            raise SecurityError("Scene graph has too many nodes.")
        return total

    def _count_tree(self, node: SceneNode) -> int:
        return 1 + sum(self._count_tree(child) for child in node.children)


class ViewportWidget(QWidget):
    """QPainter-based 2D engine viewport with pan, zoom, and scene graph rendering."""

    selection_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ViewportWidget")
        self.setMouseTracking(True)
        self.scene_graph = SceneGraph()
        self.selected_node_id: str | None = None
        self.show_grid = True
        self.snap_to_grid = False
        self.grid_size = 25.0
        self._view_offset = QPointF(0.0, 0.0)
        self._zoom_level = 1.0
        self._is_panning = False
        self._is_dragging_node = False
        self._last_mouse_pos = QPointF()

        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self.update)
        self._timer.start()

    def add_node(self, node: SceneNode) -> None:
        """Add a node to the scene and schedule a repaint."""
        self.scene_graph.add_node(node)
        self.update()

    def select_node(self, node_id: str | None) -> None:
        """Select a scene node by id, or clear selection with None."""
        if node_id is not None and self.scene_graph.find_node(node_id) is None:
            raise SecurityError(f"Unknown scene node id: {node_id}")
        self.selected_node_id = node_id
        self.selection_changed.emit(node_id or "")
        self.update()

    def selected_node(self) -> SceneNode | None:
        """Return the selected scene node, if any."""
        if self.selected_node_id is None:
            return None
        return self.scene_graph.find_node(self.selected_node_id)

    def hit_test(self, world_point: QPointF) -> SceneNode | None:
        """Return the topmost node under a world-space point."""
        for node in reversed(self.scene_graph.nodes):
            hit = self._hit_test_node(node, world_point)
            if hit is not None:
                return hit
        return None

    def move_selected_by(self, delta_x: float, delta_y: float) -> bool:
        """Move the selected node by a world-space delta."""
        node = self.selected_node()
        if node is None:
            return False
        self._move_node_tree(node, float(delta_x), float(delta_y))
        if self.snap_to_grid:
            old_x, old_y = node.x, node.y
            node.x = round(node.x / self.grid_size) * self.grid_size
            node.y = round(node.y / self.grid_size) * self.grid_size
            self._move_children(node, node.x - old_x, node.y - old_y)
        self.update()
        return True

    def duplicate_selected(self, suffix: str = "_copy") -> SceneNode | None:
        """Duplicate the selected node tree and select the duplicate root."""
        node = self.selected_node()
        if node is None:
            return None
        duplicate = SceneNode.from_dict(node.to_dict())
        duplicate.node_id = self._unique_node_id(f"{node.node_id}{suffix}")
        duplicate.display_name = f"{node.display_name or node.node_id} Copy"
        duplicate.x += self.grid_size
        duplicate.y += self.grid_size
        self._rename_child_tree(duplicate)
        self.scene_graph.add_node(duplicate)
        self.select_node(duplicate.node_id)
        self.update()
        return duplicate

    def delete_selected(self) -> bool:
        """Delete the selected node tree."""
        if self.selected_node_id is None:
            return False
        deleted = self.scene_graph.remove_node(self.selected_node_id)
        if deleted:
            self.selected_node_id = None
            self.selection_changed.emit("")
            self.update()
        return deleted

    def set_grid(self, visible: bool, grid_size: float | None = None, snap: bool | None = None) -> None:
        """Configure viewport grid visibility, size, and snapping."""
        if grid_size is not None:
            if grid_size <= 0:
                raise SecurityError("Grid size must be positive.")
            self.grid_size = float(grid_size)
        self.show_grid = bool(visible)
        if snap is not None:
            self.snap_to_grid = bool(snap)
        self.update()

    def set_view(self, offset_x: float, offset_y: float, zoom_level: float) -> None:
        """Set viewport transform values with zoom clamping."""
        self._view_offset = QPointF(float(offset_x), float(offset_y))
        self._zoom_level = self._clamp_zoom(float(zoom_level))
        self.update()

    def pan_by(self, delta_x: float, delta_y: float) -> None:
        """Move the viewport by a screen-space delta."""
        self._view_offset += QPointF(float(delta_x), float(delta_y))
        self.update()

    def zoom_by(self, factor: float, anchor: QPointF | None = None) -> None:
        """Zoom around a screen-space anchor point."""
        if factor <= 0:
            raise SecurityError("Zoom factor must be positive.")
        anchor = anchor if anchor is not None else QPointF(self.width() / 2.0, self.height() / 2.0)
        before = self.screen_to_world(anchor)
        self._zoom_level = self._clamp_zoom(self._zoom_level * factor)
        after = self.screen_to_world(anchor)
        self._view_offset += QPointF((after.x() - before.x()) * self._zoom_level, (after.y() - before.y()) * self._zoom_level)
        self.update()

    def screen_to_world(self, point: QPointF) -> QPointF:
        """Convert a screen-space point to scene coordinates."""
        return QPointF(
            (point.x() - self._view_offset.x()) / self._zoom_level,
            (point.y() - self._view_offset.y()) / self._zoom_level,
        )

    def world_to_screen(self, point: QPointF) -> QPointF:
        """Convert a scene-space point to screen coordinates."""
        return QPointF(
            point.x() * self._zoom_level + self._view_offset.x(),
            point.y() * self._zoom_level + self._view_offset.y(),
        )

    def reset_camera(self) -> None:
        """Reset the viewport camera to the default transform."""
        self.set_view(0.0, 0.0, 1.0)

    def zoom_to_fit(self, margin: float = 40.0) -> None:
        """Fit all top-level scene nodes into the current viewport."""
        if not self.scene_graph.nodes or self.width() <= 0 or self.height() <= 0:
            self.reset_camera()
            return
        left = min(node.x for node in self.scene_graph.nodes)
        top = min(node.y for node in self.scene_graph.nodes)
        right = max(node.x + node.width for node in self.scene_graph.nodes)
        bottom = max(node.y + node.height for node in self.scene_graph.nodes)
        scene_width = max(right - left, 1.0)
        scene_height = max(bottom - top, 1.0)
        available_width = max(self.width() - margin * 2.0, 1.0)
        available_height = max(self.height() - margin * 2.0, 1.0)
        zoom = self._clamp_zoom(min(available_width / scene_width, available_height / scene_height))
        offset_x = (self.width() - scene_width * zoom) / 2.0 - left * zoom
        offset_y = (self.height() - scene_height * zoom) / 2.0 - top * zoom
        self.set_view(offset_x, offset_y, zoom)

    def get_state(self) -> dict[str, Any]:
        """Serialize viewport transform and scene graph state."""
        return {
            "view_offset": {"x": self._view_offset.x(), "y": self._view_offset.y()},
            "zoom_level": self._zoom_level,
            "selected_node_id": self.selected_node_id,
            "show_grid": self.show_grid,
            "snap_to_grid": self.snap_to_grid,
            "grid_size": self.grid_size,
            "scene_graph": self.scene_graph.to_dict(),
        }

    def load_state(self, state: dict[str, Any]) -> None:
        """Restore viewport transform and scene graph state."""
        if not isinstance(state, dict):
            raise SecurityError("Viewport state must be an object.")
        offset = state.get("view_offset", {})
        if not isinstance(offset, dict):
            raise SecurityError("Viewport offset must be an object.")
        self._view_offset = QPointF(float(offset.get("x", 0.0)), float(offset.get("y", 0.0)))
        self._zoom_level = self._clamp_zoom(float(state.get("zoom_level", 1.0)))
        self.show_grid = bool(state.get("show_grid", True))
        self.snap_to_grid = bool(state.get("snap_to_grid", False))
        self.grid_size = float(state.get("grid_size", 25.0))
        self.scene_graph.load_state(state.get("scene_graph", {"nodes": []}))
        selected_node_id = state.get("selected_node_id")
        self.selected_node_id = selected_node_id if isinstance(selected_node_id, str) and self.scene_graph.find_node(selected_node_id) is not None else None
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        """Render the scene graph with the current pan/zoom transform."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(event.rect(), QColor("#171717"))
        transform = QTransform()
        transform.translate(self._view_offset.x(), self._view_offset.y())
        transform.scale(self._zoom_level, self._zoom_level)
        painter.setTransform(transform)
        if self.show_grid:
            self._draw_grid(painter)
        for node in self.scene_graph.nodes:
            self._draw_node(painter, node)
        painter.end()

    def wheelEvent(self, event: QWheelEvent) -> None:
        """Zoom the viewport with the mouse wheel."""
        factor = 1.1 if event.angleDelta().y() > 0 else 1.0 / 1.1
        self.zoom_by(factor, event.position())
        event.accept()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Begin viewport panning when middle mouse or Ctrl-drag is used."""
        if event.button() == Qt.MouseButton.MiddleButton or event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self._is_panning = True
            self._last_mouse_pos = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            node = self.hit_test(self.screen_to_world(event.position()))
            self.select_node(node.node_id if node is not None else None)
            self._is_dragging_node = node is not None
            self._last_mouse_pos = event.position()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """Update viewport panning during drag."""
        if self._is_panning:
            delta = event.position() - self._last_mouse_pos
            self.pan_by(delta.x(), delta.y())
            self._last_mouse_pos = event.position()
            event.accept()
            return
        if self._is_dragging_node:
            delta = event.position() - self._last_mouse_pos
            self.move_selected_by(delta.x() / self._zoom_level, delta.y() / self._zoom_level)
            self._last_mouse_pos = event.position()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """End viewport panning."""
        if self._is_panning:
            self._is_panning = False
            self.unsetCursor()
            event.accept()
            return
        if self._is_dragging_node:
            self._is_dragging_node = False
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _draw_grid(self, painter: QPainter) -> None:
        pen = QPen(QColor("#2a2d2e"), 1.0 / self._zoom_level)
        painter.setPen(pen)
        world_top_left = self.screen_to_world(QPointF(0.0, 0.0))
        world_bottom_right = self.screen_to_world(QPointF(float(self.width()), float(self.height())))
        start_x = int(world_top_left.x() // self.grid_size) * self.grid_size
        start_y = int(world_top_left.y() // self.grid_size) * self.grid_size
        x = start_x
        while x <= world_bottom_right.x():
            painter.drawLine(QPointF(x, world_top_left.y()), QPointF(x, world_bottom_right.y()))
            x += self.grid_size
        y = start_y
        while y <= world_bottom_right.y():
            painter.drawLine(QPointF(world_top_left.x(), y), QPointF(world_bottom_right.x(), y))
            y += self.grid_size

    def _draw_node(self, painter: QPainter, node: SceneNode) -> None:
        color = QColor(node.color)
        painter.setPen(QPen(color, 2.0))
        painter.setBrush(color if node.shape in {"rectangle", "circle", "polygon"} else Qt.BrushStyle.NoBrush)
        rect = QRectF(node.x, node.y, node.width, node.height)
        if node.shape == "rectangle":
            painter.drawRect(rect)
        elif node.shape == "circle":
            painter.drawEllipse(rect)
        elif node.shape == "line":
            painter.drawLine(QPointF(node.x, node.y), QPointF(node.x + node.width, node.y + node.height))
        elif node.shape == "polygon":
            points = node.points or [(node.x, node.y), (node.x + node.width, node.y), (node.x + node.width / 2.0, node.y + node.height)]
            painter.drawPolygon(QPolygonF([QPointF(x, y) for x, y in points]))
        if node.node_id == self.selected_node_id:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor("#ffd166"), 2.0 / self._zoom_level, Qt.PenStyle.DashLine))
            painter.drawRect(rect.adjusted(-4.0, -4.0, 4.0, 4.0))
        for child in node.children:
            self._draw_node(painter, child)

    def _hit_test_node(self, node: SceneNode, world_point: QPointF) -> SceneNode | None:
        for child in reversed(node.children):
            child_hit = self._hit_test_node(child, world_point)
            if child_hit is not None:
                return child_hit
        rect = QRectF(node.x, node.y, max(node.width, 1.0), max(node.height, 1.0))
        if rect.contains(world_point):
            return node
        return None

    def _move_node_tree(self, node: SceneNode, delta_x: float, delta_y: float) -> None:
        node.x += delta_x
        node.y += delta_y
        self._move_children(node, delta_x, delta_y)

    def _move_children(self, node: SceneNode, delta_x: float, delta_y: float) -> None:
        for child in node.children:
            child.x += delta_x
            child.y += delta_y
            self._move_children(child, delta_x, delta_y)

    def _unique_node_id(self, base: str) -> str:
        candidate = base
        index = 2
        while self.scene_graph.find_node(candidate) is not None:
            candidate = f"{base}_{index}"
            index += 1
        return candidate

    def _rename_child_tree(self, node: SceneNode) -> None:
        for child in node.children:
            child.node_id = self._unique_node_id(f"{child.node_id}_copy")
            child.display_name = f"{child.display_name or child.node_id} Copy"
            self._rename_child_tree(child)

    def _clamp_zoom(self, zoom_level: float) -> float:
        return max(MIN_ZOOM, min(MAX_ZOOM, zoom_level))
