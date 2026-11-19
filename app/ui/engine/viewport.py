# ==============================================================================
# Program: app/ui/engine/viewport.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: the scene model and the canvas of the Engine workspace. SceneNode is one drawable object
# (a rectangle, circle, line or polygon) with physics settings; SceneGraph owns the tree of nodes
# and enforces size/shape limits; ViewportWidget draws the graph with QPainter and handles pan,
# zoom, selection, dragging, grid snapping, duplicate and delete.
# Used by: app/ui/engine/workspace.py (creates one ViewportWidget and mirrors scene nodes into the
# physics world), panels.py (tree/inspector), scene_io.py (validation of imported files),
# tests/test_engine_viewport.py.
# Coordinate systems: WORLD coordinates belong to the scene (pixels, y grows downward). SCREEN
# coordinates are widget pixels. screen = world * zoom + view_offset; screen_to_world and
# world_to_screen convert; zoom is clamped to 0.1..8.
# Security: scene data can come from project/scene files, so every numeric field has a bound and
# every collection a size limit (constants below); violations raise SecurityError.
# Known limits / bugs noticed: (1) a 16 ms timer repaints the widget about 60 times per second even
# when nothing changes (simple, but wasteful); (2) the "id too long" check reports the message "must
# not be empty"; (3) the node "line" shape is drawn from (x, y) to (x + width, y + height); (4)
# hit testing uses each node's bounding box, not the exact polygon or circle shape; (5) there is no
# rotation.
# ==============================================================================

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPaintEvent, QPen, QPolygonF, QTransform, QWheelEvent
from PySide6.QtWidgets import QWidget

from app.security import SecurityError


# Shapes the renderer knows how to draw; anything else is rejected at load time.
ALLOWED_SHAPES = {"rectangle", "circle", "line", "polygon"}
# Zoom range: 10 percent to 800 percent.
MIN_ZOOM = 0.1
MAX_ZOOM = 8.0
# Safety limits, chosen generously for hand-made scenes but small enough to stop crafted files from exhausting memory or time. Depth bounds recursion; polygon points, id/name lengths and the numeric bounds below protect the renderer and physics from absurd values.
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
        """Validate every field; raises SecurityError on the first problem.

        Runs automatically after construction and is called again by SceneGraph when a tree is added, so nodes edited after creation are re-checked.
        """
        # Id is the node's unique key in the graph and in saved files.
        if not self.node_id or len(self.node_id) > MAX_NODE_ID_LENGTH:
            raise SecurityError("Scene node id must not be empty.")
        if self.display_name is not None and len(self.display_name) > MAX_DISPLAY_NAME_LENGTH:
            raise SecurityError("Scene node display name is too long.")
        # Unknown shapes would have no drawing or physics meaning.
        if self.shape not in ALLOWED_SHAPES:
            raise SecurityError(f"Unsupported scene node shape: {self.shape}")
        # Keeps coordinates within +/-1,000,000 px so float maths in the renderer stays precise.
        if abs(self.x) > MAX_ABS_COORDINATE or abs(self.y) > MAX_ABS_COORDINATE:
            raise SecurityError("Scene node coordinates are out of bounds.")
        # Sizes must be 0..100,000; negative sizes would invert boxes.
        if self.width < 0 or self.height < 0 or self.width > MAX_NODE_SIZE or self.height > MAX_NODE_SIZE:
            raise SecurityError("Scene node dimensions must not be negative.")
        # Qt decides what a valid colour string is (hex or CSS names), so a bad value cannot reach the painter.
        if not QColor(self.color).isValid():
            raise SecurityError("Scene node color must be a valid color.")
        if abs(self.velocity_x) > MAX_VELOCITY or abs(self.velocity_y) > MAX_VELOCITY:
            raise SecurityError("Scene node velocity is out of bounds.")
        # Mass is a divisor in the collision response, so it must be strictly positive.
        if self.mass <= 0 or self.mass > MAX_MASS:
            raise SecurityError("Scene node mass must be positive.")
        if self.collider_width is not None and (self.collider_width < 0 or self.collider_width > MAX_NODE_SIZE):
            raise SecurityError("Collider width must not be negative.")
        if self.collider_height is not None and (self.collider_height < 0 or self.collider_height > MAX_NODE_SIZE):
            raise SecurityError("Collider height must not be negative.")
        # Material ranges mirror the spin boxes in the properties panel: restitution and friction 0..1, damping 0..10, gravity scale -10..10, layers 0..64.
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
        # Polygon point list is bounded (256) and each point is range-checked.
        if len(self.points) > MAX_POLYGON_POINTS:
            raise SecurityError("Scene node has too many polygon points.")
        for point_x, point_y in self.points:
            if abs(point_x) > MAX_ABS_COORDINATE or abs(point_y) > MAX_ABS_COORDINATE:
                raise SecurityError("Scene node polygon point is out of bounds.")

    # Serialize this node and all children (plain dict/list/number types suitable for JSON).
    def to_dict(self) -> dict[str, Any]:
        """Serialize this node and all children."""
        return {
            "node_id": self.node_id,
            # Falls back to the id so saved files always have a readable name.
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
            # Tuples become lists because JSON has no tuple type.
            "points": [[x, y] for x, y in self.points],
            "children": [child.to_dict() for child in self.children],
        }

    # Restore a scene node (and children recursively) from serialized state.
    #
    # Missing keys take the same defaults as the dataclass. Values are coerced with float/int/bool/str and then validated by __post_init__.
    #
    # Raises:
    #     SecurityError: wrong container types, too many points or any invalid field value.
    @classmethod
    def from_dict(cls, state: dict[str, Any]) -> "SceneNode":
        """Restore a scene node from serialized state."""
        if not isinstance(state, dict):
            raise SecurityError("Scene node state must be an object.")
        # Check type and length BEFORE converting so a huge list is rejected cheaply.
        raw_points = state.get("points", [])
        if not isinstance(raw_points, list):
            raise SecurityError("Scene node points must be a list.")
        if len(raw_points) > MAX_POLYGON_POINTS:
            raise SecurityError("Scene node has too many polygon points.")
        points = [(float(point[0]), float(point[1])) for point in raw_points]
        raw_children = state.get("children", [])
        if not isinstance(raw_children, list):
            raise SecurityError("Scene node children must be a list.")
        # Recursion; total depth is checked later by SceneGraph._validate_tree.
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
        """Create an empty graph (a list of top-level nodes; children live inside each node)."""
        self._nodes: list[SceneNode] = []

    # Return immutable access to top-level nodes (a tuple copy, so callers cannot append or delete).
    @property
    def nodes(self) -> tuple[SceneNode, ...]:
        """Return immutable access to top-level nodes."""
        return tuple(self._nodes)

    # Add a top-level node (with its subtree).
    #
    # Raises:
    #     SecurityError: duplicate id (anywhere in the graph), invalid node, tree too deep or too large.
    def add_node(self, node: SceneNode) -> None:
        """Add a top-level node to the graph."""
        if self.find_node(node.node_id) is not None:
            raise SecurityError(f"Duplicate scene node id: {node.node_id}")
        # Validates the NEW subtree against ids already present, before it is attached.
        self._validate_tree(node, seen={existing.node_id for existing in self._nodes}, depth=1)
        self._nodes.append(node)

    # Remove a top-level or child node by id (its whole subtree goes with it). Returns False if not found.
    def remove_node(self, node_id: str) -> bool:
        """Remove a top-level or child node by id."""
        for index, node in enumerate(self._nodes):
            if node.node_id == node_id:
                del self._nodes[index]
                return True
            if self._remove_child(node, node_id):
                return True
        return False

    # Find a node by id anywhere in the graph (depth-first), or None.
    def find_node(self, node_id: str) -> SceneNode | None:
        """Find a node by id anywhere in the graph."""
        for node in self._nodes:
            found = self._find_in_tree(node, node_id)
            if found is not None:
                return found
        return None

    # Serialize the scene graph.
    def to_dict(self) -> dict[str, Any]:
        """Serialize the scene graph."""
        return {"nodes": [node.to_dict() for node in self._nodes]}

    # Replace the graph with the serialized state.
    #
    # Raises:
    #     SecurityError: wrong types, duplicate ids, depth over MAX_SCENE_DEPTH or more than MAX_SCENE_NODES nodes. On error the existing graph is left unchanged (the new list is only assigned at the end).
    def load_state(self, state: dict[str, Any]) -> None:
        """Restore the graph from serialized state."""
        if not isinstance(state, dict):
            raise SecurityError("Scene graph state must be an object.")
        raw_nodes = state.get("nodes", [])
        if not isinstance(raw_nodes, list):
            raise SecurityError("Scene graph nodes must be a list.")
        # Build and validate every node first, so a bad file never leaves a half-loaded scene.
        nodes = [SceneNode.from_dict(node_state) for node_state in raw_nodes]
        # One shared set of ids across all top-level trees detects duplicates between them.
        seen: set[str] = set()
        total = 0
        for node in nodes:
            total += self._validate_tree(node, seen=seen, depth=1)
        if total > MAX_SCENE_NODES:
            raise SecurityError("Scene graph has too many nodes.")
        self._nodes = nodes

    # Return the total number of nodes in the graph.
    def node_count(self) -> int:
        """Return the total number of nodes in the graph."""
        return sum(self._count_tree(node) for node in self._nodes)

    # Recursive depth-first search below `node`.
    def _find_in_tree(self, node: SceneNode, node_id: str) -> SceneNode | None:
        if node.node_id == node_id:
            return node
        for child in node.children:
            found = self._find_in_tree(child, node_id)
            if found is not None:
                return found
        return None

    # Recursively search `node`'s descendants and delete the first match.
    def _remove_child(self, node: SceneNode, node_id: str) -> bool:
        for index, child in enumerate(node.children):
            if child.node_id == node_id:
                del node.children[index]
                return True
            if self._remove_child(child, node_id):
                return True
        return False

    # Re-validate a subtree: depth limit, per-node field checks, unique ids (tracked in `seen`) and a node-count limit. Returns the number of nodes in the subtree.
    def _validate_tree(self, node: SceneNode, seen: set[str], depth: int) -> int:
        if depth > MAX_SCENE_DEPTH:
            raise SecurityError("Scene graph hierarchy is too deep.")
        # Re-run field validation so nodes mutated after construction are re-checked.
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

    # Count `node` plus all its descendants.
    def _count_tree(self, node: SceneNode) -> int:
        return 1 + sum(self._count_tree(child) for child in node.children)


class ViewportWidget(QWidget):
    """QPainter-based 2D engine viewport with pan, zoom, and scene graph rendering."""

    selection_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        """Create the canvas and start the repaint timer.

        Args:
            parent: optional Qt parent.
        """
        super().__init__(parent)
        self.setObjectName("ViewportWidget")
        # Receive mouse-move events even with no button pressed.
        self.setMouseTracking(True)
        self.scene_graph = SceneGraph()
        # Id of the selected node, or None.
        self.selected_node_id: str | None = None
        # Grid options: visible, snapping to grid on moves (off by default), spacing in world pixels.
        self.show_grid = True
        self.snap_to_grid = False
        self.grid_size = 25.0
        # Camera: translation in screen pixels and zoom factor.
        self._view_offset = QPointF(0.0, 0.0)
        self._zoom_level = 1.0
        # Interaction state set by mouse press/release: panning the camera, or dragging a node.
        self._is_panning = False
        self._is_dragging_node = False
        self._last_mouse_pos = QPointF()

        # Repaint about 60 times per second (16 ms) so the viewport shows physics motion driven elsewhere.
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self.update)
        self._timer.start()

    # Add a node to the scene and schedule a repaint.
    def add_node(self, node: SceneNode) -> None:
        """Add a node to the scene and schedule a repaint."""
        self.scene_graph.add_node(node)
        self.update()

    # Select a scene node by id, or clear selection with None.
    #
    # Raises:
    #     SecurityError: unknown id.
    def select_node(self, node_id: str | None) -> None:
        """Select a scene node by id, or clear selection with None."""
        if node_id is not None and self.scene_graph.find_node(node_id) is None:
            raise SecurityError(f"Unknown scene node id: {node_id}")
        self.selected_node_id = node_id
        # The signal carries a str, so "no selection" is the empty string.
        self.selection_changed.emit(node_id or "")
        self.update()

    # Return the selected scene node, if any.
    def selected_node(self) -> SceneNode | None:
        """Return the selected scene node, if any."""
        if self.selected_node_id is None:
            return None
        return self.scene_graph.find_node(self.selected_node_id)

    # Return the topmost node under a world-space point.
    def hit_test(self, world_point: QPointF) -> SceneNode | None:
        """Return the topmost node under a world-space point."""
        # Later nodes are drawn on top, so test them first; children are tested before their parent.
        for node in reversed(self.scene_graph.nodes):
            hit = self._hit_test_node(node, world_point)
            if hit is not None:
                return hit
        return None

    # Move the selected node (and its children) by a world-space delta. Returns False if nothing is selected.
    def move_selected_by(self, delta_x: float, delta_y: float) -> bool:
        """Move the selected node by a world-space delta."""
        node = self.selected_node()
        if node is None:
            return False
        self._move_node_tree(node, float(delta_x), float(delta_y))
        # Snap AFTER moving: the new position is rounded to the nearest grid line, and the children are shifted by the same correction so the group stays rigid.
        if self.snap_to_grid:
            old_x, old_y = node.x, node.y
            node.x = round(node.x / self.grid_size) * self.grid_size
            node.y = round(node.y / self.grid_size) * self.grid_size
            self._move_children(node, node.x - old_x, node.y - old_y)
        self.update()
        return True

    # Duplicate the selected node tree and select the duplicate root.
    #
    # Returns:
    #     The new node, or None if nothing is selected.
    def duplicate_selected(self, suffix: str = "_copy") -> SceneNode | None:
        """Duplicate the selected node tree and select the duplicate root."""
        node = self.selected_node()
        if node is None:
            return None
        # Round trip through a dict makes a deep copy of the whole subtree.
        duplicate = SceneNode.from_dict(node.to_dict())
        duplicate.node_id = self._unique_node_id(f"{node.node_id}{suffix}")
        duplicate.display_name = f"{node.display_name or node.node_id} Copy"
        # Offset by one grid cell so the copy is visible next to the original.
        duplicate.x += self.grid_size
        duplicate.y += self.grid_size
        # Children need new unique ids too, since ids are global in the graph.
        self._rename_child_tree(duplicate)
        self.scene_graph.add_node(duplicate)
        self.select_node(duplicate.node_id)
        self.update()
        return duplicate

    # Delete the selected node tree and clear the selection. Returns True if something was deleted.
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

    # Configure viewport grid visibility, size, and snapping.
    #
    # Raises:
    #     SecurityError: non-positive grid size.
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

    # Set viewport transform values with zoom clamping.
    #
    # Args:
    #     offset_x, offset_y: camera translation in screen pixels.
    #     zoom_level: requested zoom; clamped to MIN_ZOOM..MAX_ZOOM.
    def set_view(self, offset_x: float, offset_y: float, zoom_level: float) -> None:
        """Set viewport transform values with zoom clamping."""
        self._view_offset = QPointF(float(offset_x), float(offset_y))
        self._zoom_level = self._clamp_zoom(float(zoom_level))
        self.update()

    # Move the viewport by a screen-space delta.
    def pan_by(self, delta_x: float, delta_y: float) -> None:
        """Move the viewport by a screen-space delta."""
        self._view_offset += QPointF(float(delta_x), float(delta_y))
        self.update()

    # Zoom around a screen-space anchor point (default: the widget centre).
    #
    # Raises:
    #     SecurityError: non-positive factor.
    def zoom_by(self, factor: float, anchor: QPointF | None = None) -> None:
        """Zoom around a screen-space anchor point."""
        if factor <= 0:
            raise SecurityError("Zoom factor must be positive.")
        anchor = anchor if anchor is not None else QPointF(self.width() / 2.0, self.height() / 2.0)
        # Remember which world point is under the anchor, zoom, then shift the offset so that same world point is again under the anchor (so zooming feels attached to the cursor).
        before = self.screen_to_world(anchor)
        self._zoom_level = self._clamp_zoom(self._zoom_level * factor)
        after = self.screen_to_world(anchor)
        self._view_offset += QPointF((after.x() - before.x()) * self._zoom_level, (after.y() - before.y()) * self._zoom_level)
        self.update()

    # Convert a screen-space point to scene coordinates (inverse of world_to_screen).
    def screen_to_world(self, point: QPointF) -> QPointF:
        """Convert a screen-space point to scene coordinates."""
        return QPointF(
            (point.x() - self._view_offset.x()) / self._zoom_level,
            (point.y() - self._view_offset.y()) / self._zoom_level,
        )

    # Convert a scene-space point to screen coordinates: world * zoom + offset.
    def world_to_screen(self, point: QPointF) -> QPointF:
        """Convert a scene-space point to screen coordinates."""
        return QPointF(
            point.x() * self._zoom_level + self._view_offset.x(),
            point.y() * self._zoom_level + self._view_offset.y(),
        )

    # Reset the viewport camera to the default transform.
    def reset_camera(self) -> None:
        """Reset the viewport camera to the default transform."""
        self.set_view(0.0, 0.0, 1.0)

    # Fit all top-level scene nodes into the current viewport.
    #
    # Args:
    #     margin: free space in pixels kept around the content.
    def zoom_to_fit(self, margin: float = 40.0) -> None:
        """Fit all top-level scene nodes into the current viewport."""
        # Nothing to fit (or a widget with no size yet): fall back to the default camera.
        if not self.scene_graph.nodes or self.width() <= 0 or self.height() <= 0:
            self.reset_camera()
            return
        # Bounding box of all top-level nodes.
        left = min(node.x for node in self.scene_graph.nodes)
        top = min(node.y for node in self.scene_graph.nodes)
        right = max(node.x + node.width for node in self.scene_graph.nodes)
        bottom = max(node.y + node.height for node in self.scene_graph.nodes)
        scene_width = max(right - left, 1.0)
        scene_height = max(bottom - top, 1.0)
        available_width = max(self.width() - margin * 2.0, 1.0)
        available_height = max(self.height() - margin * 2.0, 1.0)
        # Largest zoom at which both width and height fit (the smaller of the two ratios).
        zoom = self._clamp_zoom(min(available_width / scene_width, available_height / scene_height))
        # Centre the content: free space split in half, minus the scaled left edge.
        offset_x = (self.width() - scene_width * zoom) / 2.0 - left * zoom
        offset_y = (self.height() - scene_height * zoom) / 2.0 - top * zoom
        self.set_view(offset_x, offset_y, zoom)

    # Serialize viewport transform and scene graph state.
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

    # Restore viewport transform and scene graph state.
    #
    # Raises:
    #     SecurityError: wrong container types or an invalid scene graph.
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
        # Keep the saved selection only when that node still exists in the loaded graph.
        self.selected_node_id = selected_node_id if isinstance(selected_node_id, str) and self.scene_graph.find_node(selected_node_id) is not None else None
        self.update()

    # Render the scene graph with the current pan/zoom transform.
    def paintEvent(self, event: QPaintEvent) -> None:
        """Render the scene graph with the current pan/zoom transform."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(event.rect(), QColor("#171717"))
        # Translate then scale builds the world -> screen mapping used by all drawing calls, so nodes are drawn in world coordinates directly.
        transform = QTransform()
        transform.translate(self._view_offset.x(), self._view_offset.y())
        transform.scale(self._zoom_level, self._zoom_level)
        painter.setTransform(transform)
        if self.show_grid:
            self._draw_grid(painter)
        for node in self.scene_graph.nodes:
            self._draw_node(painter, node)
        painter.end()

    # Zoom the viewport with the mouse wheel (10 percent per step, anchored at the pointer).
    def wheelEvent(self, event: QWheelEvent) -> None:
        """Zoom the viewport with the mouse wheel."""
        factor = 1.1 if event.angleDelta().y() > 0 else 1.0 / 1.1
        self.zoom_by(factor, event.position())
        event.accept()

    # Start panning (middle button or Ctrl+left) or select/drag a node (left button).
    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Begin viewport panning when middle mouse or Ctrl-drag is used."""
        if event.button() == Qt.MouseButton.MiddleButton or event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self._is_panning = True
            self._last_mouse_pos = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        # Left click selects the node under the cursor (or clears selection on empty space) and starts a drag if a node was hit.
        if event.button() == Qt.MouseButton.LeftButton:
            node = self.hit_test(self.screen_to_world(event.position()))
            self.select_node(node.node_id if node is not None else None)
            self._is_dragging_node = node is not None
            self._last_mouse_pos = event.position()
            event.accept()
            return
        super().mousePressEvent(event)

    # Continue panning or node dragging.
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
            # Screen delta divided by zoom gives the world-space delta, so the node follows the cursor at any zoom.
            self.move_selected_by(delta.x() / self._zoom_level, delta.y() / self._zoom_level)
            self._last_mouse_pos = event.position()
            event.accept()
            return
        super().mouseMoveEvent(event)

    # End panning or node dragging.
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

    # Draw grid lines covering the visible world rectangle.
    def _draw_grid(self, painter: QPainter) -> None:
        # Line width divided by zoom keeps lines 1 screen pixel wide at any zoom.
        pen = QPen(QColor("#2a2d2e"), 1.0 / self._zoom_level)
        painter.setPen(pen)
        world_top_left = self.screen_to_world(QPointF(0.0, 0.0))
        world_bottom_right = self.screen_to_world(QPointF(float(self.width()), float(self.height())))
        # Start at the first grid line left of/above the visible area, aligned to multiples of grid_size.
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

    # Draw one node (and children) in world coordinates; the selected node gets a dashed yellow outline slightly larger than its box.
    def _draw_node(self, painter: QPainter, node: SceneNode) -> None:
        color = QColor(node.color)
        painter.setPen(QPen(color, 2.0))
        # Rectangles, circles and polygons are filled; lines have no fill.
        painter.setBrush(color if node.shape in {"rectangle", "circle", "polygon"} else Qt.BrushStyle.NoBrush)
        rect = QRectF(node.x, node.y, node.width, node.height)
        if node.shape == "rectangle":
            painter.drawRect(rect)
        elif node.shape == "circle":
            painter.drawEllipse(rect)
        elif node.shape == "line":
            painter.drawLine(QPointF(node.x, node.y), QPointF(node.x + node.width, node.y + node.height))
        elif node.shape == "polygon":
            # A polygon with no explicit points draws a default triangle inside its box.
            points = node.points or [(node.x, node.y), (node.x + node.width, node.y), (node.x + node.width / 2.0, node.y + node.height)]
            painter.drawPolygon(QPolygonF([QPointF(x, y) for x, y in points]))
        if node.node_id == self.selected_node_id:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor("#ffd166"), 2.0 / self._zoom_level, Qt.PenStyle.DashLine))
            painter.drawRect(rect.adjusted(-4.0, -4.0, 4.0, 4.0))
        for child in node.children:
            self._draw_node(painter, child)

    # Test children before the node itself (children are drawn on top); uses the bounding box with at least 1 px size so zero-size nodes can still be clicked.
    def _hit_test_node(self, node: SceneNode, world_point: QPointF) -> SceneNode | None:
        for child in reversed(node.children):
            child_hit = self._hit_test_node(child, world_point)
            if child_hit is not None:
                return child_hit
        rect = QRectF(node.x, node.y, max(node.width, 1.0), max(node.height, 1.0))
        if rect.contains(world_point):
            return node
        return None

    # Move a node and all its descendants by the same delta.
    def _move_node_tree(self, node: SceneNode, delta_x: float, delta_y: float) -> None:
        node.x += delta_x
        node.y += delta_y
        self._move_children(node, delta_x, delta_y)

    # Recursively move descendants (used on its own after grid snapping).
    def _move_children(self, node: SceneNode, delta_x: float, delta_y: float) -> None:
        for child in node.children:
            child.x += delta_x
            child.y += delta_y
            self._move_children(child, delta_x, delta_y)

    # Return `base`, or `base_2`, `base_3`... the first id not yet used in the graph.
    def _unique_node_id(self, base: str) -> str:
        candidate = base
        index = 2
        while self.scene_graph.find_node(candidate) is not None:
            candidate = f"{base}_{index}"
            index += 1
        return candidate

    # Give every descendant of a duplicated node a new unique id and a "Copy" name.
    def _rename_child_tree(self, node: SceneNode) -> None:
        for child in node.children:
            child.node_id = self._unique_node_id(f"{child.node_id}_copy")
            child.display_name = f"{child.display_name or child.node_id} Copy"
            self._rename_child_tree(child)

    # Clamp to MIN_ZOOM..MAX_ZOOM.
    def _clamp_zoom(self, zoom_level: float) -> float:
        return max(MIN_ZOOM, min(MAX_ZOOM, zoom_level))
