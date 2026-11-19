# ==============================================================================
# Program: app/ui/studio/photo_editor.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: the photo editor mode: a layered raster canvas with brush, eraser, rectangle, ellipse
# and line tools, per-layer opacity/visibility/blend mode, undo/redo, canvas resize, image import
# and project save/load. Image pixels live in QImage buffers (ARGB32 premultiplied).
# Structure: Layer, LayerStack and UndoStack are the data model (QImage only, no widgets, testable
# without a QApplication); PhotoEditorWidget is the canvas (draws the flattened stack, handles the
# mouse); PhotoEditorToolbar and LayersPanel are control strips bound to a canvas;
# PhotoEditorPanel composes all three and accepts images dragged from the asset manager.
# Used by: app/application.py (build_photo_editor_mode; asset_dropped routes to the same import
# path as double-clicking an image asset); tests/test_photo_editor.py.
# Safety limits: at most 64 layers, canvas sides up to 8192 px, 50 undo steps; saved layers are
# PNG data in base64 and validated on load (SecurityError for bad base64/PNG).
# Known limits / bugs noticed: shape tools give no live preview (the shape appears on mouse
# release); Layer.from_dict accepts a PNG of any size and pixel format and does not resize it to
# the declared width/height or MAX_CANVAS_DIMENSION; undo stores a snapshot per layer INDEX, so
# deleting or reordering layers makes older snapshots refer to different layers; canvas resize is
# not undoable; the wheel zooms about the origin, not the cursor.
# ==============================================================================

"""Raster drawing canvas: a pure-Python layer/undo model plus a QPainter widget.

Layer, LayerStack, and UndoStack hold and manipulate QImage buffers directly and
have no QWidget dependency, so they can be unit-tested without a QApplication.
PhotoEditorWidget wraps a LayerStack + UndoStack and adds mouse-driven drawing
tools on top.
"""
from __future__ import annotations

import base64
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPoint, QPointF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QImage,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPen,
    QShortcut,
    QTransform,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.security import SecurityError

# Limits protecting memory: a full-size layer is width x height x 4 bytes (a 8192 x 8192 layer is 256 MiB), hence the layer cap and the per-side cap.
MAX_LAYERS = 64
MAX_UNDO_DEPTH = 50
MAX_CANVAS_DIMENSION = 8192

# Blend modes offered to the user (names stored in project files).
BLEND_MODES = ("normal", "multiply", "screen")

# Maps a blend-mode name to the QPainter composition mode used when flattening.
_COMPOSITION_MODE_BY_BLEND = {
    "normal": QPainter.CompositionMode.CompositionMode_SourceOver,
    "multiply": QPainter.CompositionMode.CompositionMode_Multiply,
    "screen": QPainter.CompositionMode.CompositionMode_Screen,
}


class ToolKind(str, Enum):
    """Drawing tools. Subclassing str makes the members compare equal to their text values."""
    BRUSH = "brush"
    ERASER = "eraser"
    RECTANGLE = "rectangle"
    ELLIPSE = "ellipse"
    LINE = "line"


class Layer:
    """A single raster layer: a QImage buffer plus display metadata."""

    def __init__(self, width: int, height: int, name: str = "Layer") -> None:
        """Create a transparent layer.

        Args:
            width, height: size in pixels, 1..MAX_CANVAS_DIMENSION.
            name: display name.

        Raises:
            SecurityError: size out of range.
        """
        if not (0 < width <= MAX_CANVAS_DIMENSION and 0 < height <= MAX_CANVAS_DIMENSION):
            raise SecurityError("Layer dimensions are out of range.")
        self.name = name
        self.opacity = 1.0
        self.visible = True
        self.blend_mode = "normal"
        # Premultiplied alpha is Qt's fastest format for painting; the image starts fully transparent.
        self.image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
        self.image.fill(QColor(0, 0, 0, 0))

    def set_opacity(self, opacity: float) -> None:
        """Set layer opacity, clamped to 0.0..1.0 (the UI slider may overshoot)."""
        self.opacity = min(1.0, max(0.0, opacity))

    def set_blend_mode(self, blend_mode: str) -> None:
        """Set the blend mode.

        Raises:
            SecurityError: name not in BLEND_MODES.
        """
        if blend_mode not in BLEND_MODES:
            raise SecurityError("Unsupported blend mode.")
        self.blend_mode = blend_mode

    def snapshot(self) -> QImage:
        """Return a deep copy of the pixels (used for undo)."""
        return self.image.copy()

    def restore(self, snapshot: QImage) -> None:
        """Replace the pixels with a copy of `snapshot`."""
        self.image = snapshot.copy()

    def to_dict(self) -> dict:
        """Serialise metadata and the pixels (PNG encoded, then base64 text) for the project file."""
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        # PNG is lossless and compact for flat artwork; QBuffer lets Qt write into memory instead of a file.
        self.image.save(buffer, "PNG")
        encoded = base64.b64encode(bytes(buffer.data())).decode("ascii")
        return {
            "name": self.name,
            "opacity": self.opacity,
            "visible": self.visible,
            "blend_mode": self.blend_mode,
            "width": self.image.width(),
            "height": self.image.height(),
            "png_base64": encoded,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Layer":
        """Rebuild a layer from to_dict output.

        Raises:
            SecurityError: dimensions out of range, unknown blend mode, invalid base64 or invalid PNG.
        """
        width = int(data.get("width", 1))
        height = int(data.get("height", 1))
        layer = cls(width, height, name=str(data.get("name", "Layer")))
        layer.set_opacity(float(data.get("opacity", 1.0)))
        layer.visible = bool(data.get("visible", True))
        layer.set_blend_mode(str(data.get("blend_mode", "normal")))
        # Pixel data is optional; a missing/empty value leaves the layer transparent.
        encoded = data.get("png_base64")
        if encoded:
            try:
                # validate=True rejects stray characters instead of silently skipping them.
                raw = base64.b64decode(encoded, validate=True)
            except Exception as exc:  # binascii.Error subclasses ValueError
                raise SecurityError("Layer image data is not valid base64.") from exc
            image = QImage()
            # Qt decides if the bytes are a real PNG; garbage is rejected.
            if not image.loadFromData(QByteArray(raw), "PNG"):
                raise SecurityError("Layer image data is not a valid PNG.")
            layer.image = image
        return layer


# Ordered layers (index 0 is the bottom) sharing one canvas size.
class LayerStack:
    """Ordered stack of layers, bottom (index 0) to top."""

    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height
        self.layers: list[Layer] = []

    def add_layer(self, name: str = "") -> Layer:
        """Create a transparent layer on top and return it.

        Raises:
            SecurityError: already MAX_LAYERS layers.
        """
        if len(self.layers) >= MAX_LAYERS:
            raise SecurityError("Too many layers.")
        layer = Layer(self.width, self.height, name=name or f"Layer {len(self.layers) + 1}")
        self.layers.append(layer)
        return layer

    def remove_layer(self, index: int) -> bool:
        """Delete a layer by index; returns False for a bad index."""
        if not (0 <= index < len(self.layers)):
            return False
        del self.layers[index]
        return True

    def move_layer(self, from_index: int, to_index: int) -> bool:
        """Move a layer from one index to another (shifting those between); returns False if either index is invalid."""
        if not (0 <= from_index < len(self.layers)) or not (0 <= to_index < len(self.layers)):
            return False
        layer = self.layers.pop(from_index)
        self.layers.insert(to_index, layer)
        return True

    # Reallocates every layer's QImage at the new size, preserving
    # existing pixel content anchored at the top-left -- cropping if the
    # new size is smaller, padding with transparency if larger.
    def resize_canvas(self, width: int, height: int) -> None:
        """Reallocates every layer's QImage at the new size, preserving
        existing pixel content anchored at the top-left -- cropping if the
        new size is smaller, padding with transparency if larger."""
        if not (0 < width <= MAX_CANVAS_DIMENSION and 0 < height <= MAX_CANVAS_DIMENSION):
            raise SecurityError("Canvas dimensions are out of range.")
        # Draw the old image into a new transparent image; Qt clips whatever does not fit.
        for layer in self.layers:
            resized = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
            resized.fill(QColor(0, 0, 0, 0))
            painter = QPainter(resized)
            painter.drawImage(0, 0, layer.image)
            painter.end()
            layer.image = resized
        self.width = width
        self.height = height

    # Pure-data snapshot of each layer for driving a layers-panel UI.
    def summary(self) -> list[dict]:
        """Pure-data snapshot of each layer for driving a layers-panel UI."""
        return [
            {
                "index": index,
                "name": layer.name,
                "visible": layer.visible,
                "opacity": layer.opacity,
                "blend_mode": layer.blend_mode,
            }
            for index, layer in enumerate(self.layers)
        ]

    def flatten(self) -> QImage:
        """Composite all visible layers (bottom to top) into one new image; used by painting and export."""
        composite = QImage(self.width, self.height, QImage.Format.Format_ARGB32_Premultiplied)
        composite.fill(QColor(0, 0, 0, 0))
        painter = QPainter(composite)
        for layer in self.layers:
            # Skip layers that would contribute nothing.
            if not layer.visible or layer.opacity <= 0.0:
                continue
            # Per-layer opacity and blend mode are applied as painter state while drawing that layer.
            painter.setOpacity(layer.opacity)
            painter.setCompositionMode(_COMPOSITION_MODE_BY_BLEND[layer.blend_mode])
            painter.drawImage(0, 0, layer.image)
        painter.end()
        return composite

    def get_state(self) -> dict:
        """Serialise canvas size and all layers."""
        return {
            "width": self.width,
            "height": self.height,
            "layers": [layer.to_dict() for layer in self.layers],
        }

    def load_state(self, state: dict) -> None:
        """Replace the stack from saved state.

        Raises:
            SecurityError: more than MAX_LAYERS layers or an invalid layer.
        """
        layers_data = state.get("layers", [])
        if len(layers_data) > MAX_LAYERS:
            raise SecurityError("Too many layers in saved state.")
        self.width = int(state.get("width", self.width))
        self.height = int(state.get("height", self.height))
        self.layers = [Layer.from_dict(data) for data in layers_data]


# Undo/redo of pixel edits as (layer index, snapshot) pairs. Each entry holds a FULL copy of one layer, so memory use is up to 50 x layer size.
class UndoStack:
    """Records pre-change QImage snapshots for a layer stack, for undo/redo."""

    def __init__(self, max_depth: int = MAX_UNDO_DEPTH) -> None:
        """Create an empty history.

        Args:
            max_depth: number of undo steps kept (default MAX_UNDO_DEPTH).
        """
        self.max_depth = max_depth
        # Two stacks: snapshots to restore on undo, and snapshots to restore on redo.
        self._undo: list[tuple[int, QImage]] = []
        self._redo: list[tuple[int, QImage]] = []

    def record(self, layer_index: int, snapshot: QImage) -> None:
        """Remember the layer's pixels from BEFORE an edit; drops the oldest entry past max_depth and clears redo."""
        self._undo.append((layer_index, snapshot))
        if len(self._undo) > self.max_depth:
            self._undo.pop(0)
        self._redo.clear()

    def can_undo(self) -> bool:
        """True if an undo step exists."""
        return bool(self._undo)

    def can_redo(self) -> bool:
        """True if a redo step exists."""
        return bool(self._redo)

    def undo(self, layer_stack: LayerStack) -> Optional[int]:
        """Restore the most recent snapshot and return its layer index, or None if nothing could be undone."""
        if not self._undo:
            return None
        layer_index, snapshot = self._undo.pop()
        if not (0 <= layer_index < len(layer_stack.layers)):
            return None
        layer = layer_stack.layers[layer_index]
        # Save the current pixels so redo can come back.
        self._redo.append((layer_index, layer.snapshot()))
        layer.restore(snapshot)
        return layer_index

    def redo(self, layer_stack: LayerStack) -> Optional[int]:
        """Reapply the last undone edit and return its layer index, or None."""
        if not self._redo:
            return None
        layer_index, snapshot = self._redo.pop()
        if not (0 <= layer_index < len(layer_stack.layers)):
            return None
        layer = layer_stack.layers[layer_index]
        self._undo.append((layer_index, layer.snapshot()))
        layer.restore(snapshot)
        return layer_index


# Zoom limits for the canvas view (10 percent to 800 percent).
MIN_CANVAS_ZOOM = 0.1
MAX_CANVAS_ZOOM = 8.0


class PhotoEditorWidget(QWidget):
    """The main raster canvas: renders the flattened layer stack and hosts tools."""

    layer_changed = Signal(int)

    def __init__(self, parent: QWidget | None = None, width: int = 1024, height: int = 768) -> None:
        """Create the canvas with a transparent "Background" layer.

        Args:
            parent: optional Qt parent.
            width, height: canvas size in pixels (default 1024 x 768).
        """
        super().__init__(parent)
        self.setObjectName("PhotoEditorWidget")
        self.setMinimumSize(200, 200)
        self.layer_stack = LayerStack(width, height)
        self.layer_stack.add_layer("Background")
        self.undo_stack = UndoStack()
        # Drawing goes to the active layer.
        self.active_layer_index = 0
        self.active_tool = ToolKind.BRUSH
        self.brush_size = 8
        self.brush_color = QColor("#ffffff")
        # Stroke state: whether a stroke is in progress, the previous point (brush segments), the start point (shapes) and the layer pixels from before the stroke (for undo).
        self._drawing = False
        self._last_point: Optional[QPoint] = None
        self._shape_start: Optional[QPoint] = None
        self._pre_stroke_snapshot: Optional[QImage] = None

        # View transform and Ctrl-drag panning state.
        self._zoom_level = 1.0
        self._view_offset = QPointF(0.0, 0.0)
        self._panning = False
        self._pan_last_pos: Optional[QPoint] = None

        # Standard Undo/Redo key sequences (platform specific) act while this widget has focus.
        undo_shortcut = QShortcut(QKeySequence.StandardKey.Undo, self)
        undo_shortcut.activated.connect(self.undo)
        redo_shortcut = QShortcut(QKeySequence.StandardKey.Redo, self)
        redo_shortcut.activated.connect(self.redo)

    def _current_transform(self) -> QTransform:
        """Build the image-to-screen transform: translate by the pan offset, then scale by the zoom."""
        transform = QTransform()
        transform.translate(self._view_offset.x(), self._view_offset.y())
        transform.scale(self._zoom_level, self._zoom_level)
        return transform

    def _screen_to_image(self, pos: QPointF) -> QPoint:
        """Convert a widget position to an integer image pixel using the inverse transform."""
        inverted, invertible = self._current_transform().inverted()
        # Cannot happen with zoom >= 0.1; fall back to the raw position.
        if not invertible:
            return QPoint(int(pos.x()), int(pos.y()))
        mapped = inverted.map(QPointF(pos))
        return QPoint(int(mapped.x()), int(mapped.y()))

    def active_layer(self) -> Optional[Layer]:
        """Return the layer being edited, or None if the index is stale."""
        if 0 <= self.active_layer_index < len(self.layer_stack.layers):
            return self.layer_stack.layers[self.active_layer_index]
        return None

    def set_active_tool(self, tool: ToolKind) -> None:
        """Choose the drawing tool."""
        self.active_tool = tool

    def choose_color(self) -> None:
        """Open the system colour dialog and keep the colour if the user did not cancel."""
        color = QColorDialog.getColor(self.brush_color, self, "Choose Drawing Color")
        if color.isValid():
            self.brush_color = color

    def _begin_stroke(self, pos: QPoint) -> None:
        """Start a stroke: snapshot the layer for undo, remember the start point and paint the first dab for brush/eraser."""
        layer = self.active_layer()
        if layer is None:
            return
        self._pre_stroke_snapshot = layer.snapshot()
        self._drawing = True
        self._last_point = pos
        self._shape_start = pos
        if self.active_tool in (ToolKind.BRUSH, ToolKind.ERASER):
            self._stroke_to(pos)

    def _stroke_to(self, pos: QPoint) -> None:
        """Draw a round-capped line from the last point to `pos` on the active layer.

        The eraser uses the Clear composition mode, which removes pixels (alpha 0) instead of painting a colour.
        """
        layer = self.active_layer()
        if layer is None or self._last_point is None:
            return
        painter = QPainter(layer.image)
        if self.active_tool == ToolKind.ERASER:
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
            pen = QPen(Qt.GlobalColor.transparent, self.brush_size, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        else:
            pen = QPen(self.brush_color, self.brush_size, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawLine(self._last_point, pos)
        painter.end()
        self._last_point = pos
        self.update()

    def _finish_shape(self, pos: QPoint) -> None:
        """Draw the rectangle, ellipse or line between the press point and `pos` (called on mouse release)."""
        layer = self.active_layer()
        if layer is None or self._shape_start is None:
            return
        painter = QPainter(layer.image)
        painter.setPen(QPen(self.brush_color, self.brush_size))
        # Normalise the drag so it works in any direction (negative width/height become a positive box).
        rect_x = min(self._shape_start.x(), pos.x())
        rect_y = min(self._shape_start.y(), pos.y())
        rect_w = abs(pos.x() - self._shape_start.x())
        rect_h = abs(pos.y() - self._shape_start.y())
        if self.active_tool == ToolKind.RECTANGLE:
            painter.drawRect(rect_x, rect_y, rect_w, rect_h)
        elif self.active_tool == ToolKind.ELLIPSE:
            painter.drawEllipse(rect_x, rect_y, rect_w, rect_h)
        elif self.active_tool == ToolKind.LINE:
            painter.drawLine(self._shape_start, pos)
        painter.end()
        self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt override
        """Ctrl+left starts panning; plain left starts a stroke at the image position."""
        if event.button() == Qt.MouseButton.LeftButton:
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                self._panning = True
                self._pan_last_pos = event.position().toPoint()
                return
            self._begin_stroke(self._screen_to_image(event.position()))

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt override
        """Pan the view, or continue a brush/eraser stroke."""
        if self._panning and self._pan_last_pos is not None:
            current_pos = event.position().toPoint()
            delta = current_pos - self._pan_last_pos
            self._view_offset += QPointF(delta)
            self._pan_last_pos = current_pos
            self.update()
            return
        if self._drawing and self.active_tool in (ToolKind.BRUSH, ToolKind.ERASER):
            self._stroke_to(self._screen_to_image(event.position()))

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt override
        """Finish panning, or finish the stroke: draw shapes, record the undo snapshot and announce the change through layer_changed."""
        if self._panning:
            self._panning = False
            self._pan_last_pos = None
            return
        if not self._drawing:
            return
        pos = self._screen_to_image(event.position())
        if self.active_tool in (ToolKind.RECTANGLE, ToolKind.ELLIPSE, ToolKind.LINE):
            self._finish_shape(pos)
        if self._pre_stroke_snapshot is not None:
            self.undo_stack.record(self.active_layer_index, self._pre_stroke_snapshot)
        self._drawing = False
        self._last_point = None
        self._shape_start = None
        self._pre_stroke_snapshot = None
        self.layer_changed.emit(self.active_layer_index)

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802 - Qt override
        """Zoom the view by 10 percent per wheel step, clamped to the zoom limits."""
        delta = event.angleDelta().y()
        factor = 1.1 if delta > 0 else 0.9
        self._zoom_level = min(MAX_CANVAS_ZOOM, max(MIN_CANVAS_ZOOM, self._zoom_level * factor))
        self.update()

    def undo(self) -> None:
        """Undo the last stroke and repaint."""
        if self.undo_stack.undo(self.layer_stack) is not None:
            self.update()

    def redo(self) -> None:
        """Redo the last undone stroke and repaint."""
        if self.undo_stack.redo(self.layer_stack) is not None:
            self.update()

    def resize_canvas(self, width: int, height: int) -> None:
        """Resize every layer (see LayerStack.resize_canvas) and repaint."""
        self.layer_stack.resize_canvas(width, height)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt override
        """Draw the flattened layers with the pan/zoom transform. The whole stack is flattened on every repaint, which is simple but costly for big canvases."""
        painter = QPainter(self)
        painter.setTransform(self._current_transform())
        painter.drawImage(QPointF(0, 0), self.layer_stack.flatten())
        painter.end()

    def get_state(self) -> dict:
        """Return the layer stack state for project saving."""
        return self.layer_stack.get_state()

    def load_state(self, state: dict) -> None:
        """Load saved layers, select layer 0 and clear undo history (snapshots would not match)."""
        self.layer_stack.load_state(state)
        self.active_layer_index = 0
        self.undo_stack = UndoStack()
        self.update()

    # Loads an external image file as a new top layer, scaled to canvas size.
    # Used by the asset-manager wiring (double-click/drag an image asset).
    # Path trust follows the same convention as MainWindow.open_file(): the
    # path comes from a user-driven selection (dialog or catalogued asset),
    # and Qt's image codecs -- not arbitrary code -- do the loading.
    def import_image_as_layer(self, file_path: str) -> None:
        """Loads an external image file as a new top layer, scaled to canvas size.

        Used by the asset-manager wiring (double-click/drag an image asset).
        Path trust follows the same convention as MainWindow.open_file(): the
        path comes from a user-driven selection (dialog or catalogued asset),
        and Qt's image codecs -- not arbitrary code -- do the loading.
        """
        # A null image means unreadable or unsupported data.
        image = QImage(file_path)
        if image.isNull():
            raise SecurityError("Could not load image file.")
        layer = self.layer_stack.add_layer(name=os.path.basename(file_path))
        painter = QPainter(layer.image)
        # Source and target rectangles differ, so the image is stretched to fill the canvas (aspect ratio is not preserved).
        painter.drawImage(layer.image.rect(), image, image.rect())
        painter.end()
        self.active_layer_index = len(self.layer_stack.layers) - 1
        self.update()
        self.layer_changed.emit(self.active_layer_index)


# Strip with one toggle button per tool, a brush-size spin box, a colour swatch, Undo/Redo and Resize Canvas.
class PhotoEditorToolbar(QWidget):
    """Tool selection, brush size, color swatch, and undo/redo controls."""

    def __init__(self, editor: PhotoEditorWidget, parent: QWidget | None = None) -> None:
        """Args:
            editor: the canvas this toolbar controls.
            parent: optional Qt parent.
        """
        super().__init__(parent)
        self.setObjectName("PhotoEditorToolbar")
        self.editor = editor
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        self._tool_buttons: dict[ToolKind, QPushButton] = {}
        # Buttons act like a radio group: _select_tool unchecks the others.
        for tool in ToolKind:
            button = QPushButton(tool.value.capitalize())
            button.setObjectName("photoToolBtn")
            button.setCheckable(True)
            button.clicked.connect(lambda _checked=False, t=tool: self._select_tool(t))
            layout.addWidget(button)
            self._tool_buttons[tool] = button
        self._tool_buttons[editor.active_tool].setChecked(True)

        layout.addWidget(QLabel("Size:"))
        self.size_spin = QSpinBox()
        # Brush width in pixels.
        self.size_spin.setRange(1, 200)
        self.size_spin.setValue(editor.brush_size)
        self.size_spin.valueChanged.connect(self._on_size_changed)
        layout.addWidget(self.size_spin)

        self.color_swatch = QPushButton()
        self.color_swatch.setObjectName("colorSwatch")
        self.color_swatch.setFixedSize(24, 24)
        self.color_swatch.clicked.connect(self._on_choose_color)
        layout.addWidget(self.color_swatch)
        self._update_swatch_color()

        self.undo_btn = QPushButton("Undo")
        self.undo_btn.clicked.connect(editor.undo)
        self.redo_btn = QPushButton("Redo")
        self.redo_btn.clicked.connect(editor.redo)
        layout.addWidget(self.undo_btn)
        layout.addWidget(self.redo_btn)

        self.resize_canvas_btn = QPushButton("Resize Canvas…")
        self.resize_canvas_btn.clicked.connect(self._on_resize_canvas)
        layout.addWidget(self.resize_canvas_btn)
        layout.addStretch()

    def _on_resize_canvas(self) -> None:
        """Ask for the new width and height and resize; a rejected size is shown in a warning box."""
        # Imported here because the dialogs are only used on this rare action.
        from PySide6.QtWidgets import QInputDialog, QMessageBox

        width, ok = QInputDialog.getInt(
            self, "Resize Canvas", "Width:", self.editor.layer_stack.width, 1, MAX_CANVAS_DIMENSION
        )
        if not ok:
            return
        height, ok = QInputDialog.getInt(
            self, "Resize Canvas", "Height:", self.editor.layer_stack.height, 1, MAX_CANVAS_DIMENSION
        )
        if not ok:
            return
        try:
            self.editor.resize_canvas(width, height)
        except SecurityError as exc:
            QMessageBox.warning(self, "Resize Blocked", str(exc))

    def _select_tool(self, tool: ToolKind) -> None:
        """Activate a tool on the canvas and update the checked state of all tool buttons."""
        self.editor.set_active_tool(tool)
        for candidate, button in self._tool_buttons.items():
            button.setChecked(candidate == tool)

    def _on_size_changed(self, value: int) -> None:
        """Store the new brush size on the canvas."""
        self.editor.brush_size = value

    def _update_swatch_color(self) -> None:
        """Show the current brush colour as the swatch background."""
        self.color_swatch.setStyleSheet(f"background-color: {self.editor.brush_color.name()};")

    def _on_choose_color(self) -> None:
        """Open the colour dialog and refresh the swatch."""
        self.editor.choose_color()
        self._update_swatch_color()


# List of layers with visibility checkbox, opacity slider and add/remove/reorder buttons for the active layer.
class LayersPanel(QWidget):
    """Lists a PhotoEditorWidget's layers with visibility/opacity controls and reordering."""

    def __init__(self, editor: PhotoEditorWidget, parent: QWidget | None = None) -> None:
        """Args:
            editor: the canvas whose layers are shown.
            parent: optional Qt parent.
        """
        super().__init__(parent)
        self.setObjectName("LayersPanel")
        self.editor = editor
        self.setup_ui()
        self.refresh()

    def setup_ui(self) -> None:
        """Build the list, controls and button row."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addWidget(QLabel("Layers"))

        self.list_widget = QListWidget()
        # Selecting a row makes it the active layer.
        self.list_widget.currentRowChanged.connect(self._on_row_selected)
        layout.addWidget(self.list_widget, 1)

        self.visible_checkbox = QCheckBox("Visible")
        self.visible_checkbox.toggled.connect(self._on_visibility_toggled)
        layout.addWidget(self.visible_checkbox)

        opacity_row = QHBoxLayout()
        opacity_row.addWidget(QLabel("Opacity"))
        self.opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.opacity_slider.setRange(0, 100)
        self.opacity_slider.valueChanged.connect(self._on_opacity_changed)
        opacity_row.addWidget(self.opacity_slider)
        layout.addLayout(opacity_row)

        button_row = QHBoxLayout()
        add_btn = QPushButton("+")
        add_btn.clicked.connect(self._on_add_layer)
        remove_btn = QPushButton("-")
        remove_btn.clicked.connect(self._on_remove_layer)
        up_btn = QPushButton("Up")
        up_btn.clicked.connect(self._on_move_up)
        down_btn = QPushButton("Down")
        down_btn.clicked.connect(self._on_move_down)
        for button in (add_btn, remove_btn, up_btn, down_btn):
            button_row.addWidget(button)
        layout.addLayout(button_row)

    def refresh(self) -> None:
        """Rebuild the list from the layer stack and reselect the active layer."""
        # Block signals while rebuilding so repopulating does not trigger _on_row_selected.
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        for entry in self.editor.layer_stack.summary():
            label = f"{entry['name']} ({int(entry['opacity'] * 100)}%)"
            if not entry["visible"]:
                label += " [hidden]"
            self.list_widget.addItem(label)
        self.list_widget.blockSignals(False)

        active = self.editor.active_layer_index
        if 0 <= active < self.list_widget.count():
            self.list_widget.setCurrentRow(active)
        self._sync_controls()

    def _sync_controls(self) -> None:
        """Show the active layer's visibility and opacity in the controls without triggering their signals."""
        layer = self.editor.active_layer()
        self.visible_checkbox.blockSignals(True)
        self.opacity_slider.blockSignals(True)
        if layer is not None:
            self.visible_checkbox.setChecked(layer.visible)
            self.opacity_slider.setValue(int(layer.opacity * 100))
        self.visible_checkbox.blockSignals(False)
        self.opacity_slider.blockSignals(False)

    def _on_row_selected(self, row: int) -> None:
        """Make the chosen row the active layer."""
        if row < 0:
            return
        self.editor.active_layer_index = row
        self._sync_controls()

    def _on_visibility_toggled(self, checked: bool) -> None:
        """Show or hide the active layer."""
        layer = self.editor.active_layer()
        if layer is not None:
            layer.visible = checked
            self.editor.update()
            self.refresh()

    def _on_opacity_changed(self, value: int) -> None:
        """Set the active layer opacity from the slider (0..100 percent)."""
        layer = self.editor.active_layer()
        if layer is not None:
            layer.set_opacity(value / 100.0)
            self.editor.update()

    def _on_add_layer(self) -> None:
        """Add a layer on top and select it."""
        self.editor.layer_stack.add_layer()
        self.editor.active_layer_index = len(self.editor.layer_stack.layers) - 1
        self.editor.update()
        self.refresh()

    def _on_remove_layer(self) -> None:
        """Remove the active layer; the last remaining layer cannot be removed."""
        if len(self.editor.layer_stack.layers) <= 1:
            return
        self.editor.layer_stack.remove_layer(self.editor.active_layer_index)
        self.editor.active_layer_index = max(0, self.editor.active_layer_index - 1)
        self.editor.update()
        self.refresh()

    def _on_move_up(self) -> None:
        """Move the active layer one step towards the top of the list (index - 1)."""
        index = self.editor.active_layer_index
        if index > 0:
            self.editor.layer_stack.move_layer(index, index - 1)
            self.editor.active_layer_index = index - 1
            self.editor.update()
            self.refresh()

    def _on_move_down(self) -> None:
        """Move the active layer one step down (index + 1)."""
        index = self.editor.active_layer_index
        if index < len(self.editor.layer_stack.layers) - 1:
            self.editor.layer_stack.move_layer(index, index + 1)
            self.editor.active_layer_index = index + 1
            self.editor.update()
            self.refresh()


# Complete photo editor surface: toolbar + canvas + layers panel, composed together.
class PhotoEditorPanel(QWidget):
    """Complete photo editor surface: toolbar + canvas + layers panel, composed together."""

    # Emitted with the relative path of an asset dropped from the asset manager; the main window imports it.
    asset_dropped = Signal(str)

    def __init__(self, parent: QWidget | None = None, width: int = 1024, height: int = 768) -> None:
        """Args:
            parent: optional Qt parent.
            width, height: canvas size in pixels.
        """
        super().__init__(parent)
        self.setObjectName("PhotoEditorPanel")
        self.canvas = PhotoEditorWidget(width=width, height=height)
        self.toolbar = PhotoEditorToolbar(self.canvas)
        self.layers_panel = LayersPanel(self.canvas)
        # Keep the layers list in step with strokes and imports.
        self.canvas.layer_changed.connect(lambda _index: self.layers_panel.refresh())
        # Required for dragEnterEvent/dropEvent to be called.
        self.setAcceptDrops(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.toolbar)

        body = QHBoxLayout()
        body.addWidget(self.canvas, 1)
        body.addWidget(self.layers_panel)
        layout.addLayout(body)

    def dragEnterEvent(self, event) -> None:  # noqa: N802 - Qt override
        """Accept only drags with the asset manager MIME type."""
        from app.ui.assets.asset_manager import ASSET_MIME_TYPE

        if event.mimeData().hasFormat(ASSET_MIME_TYPE):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802 - Qt override
        """Decode the dropped asset path and emit asset_dropped; invalid payloads are ignored."""
        from app.ui.assets.asset_manager import ASSET_MIME_TYPE, decode_asset_mime_payload

        mime = event.mimeData()
        if not mime.hasFormat(ASSET_MIME_TYPE):
            return
        try:
            relative_path = decode_asset_mime_payload(bytes(mime.data(ASSET_MIME_TYPE)))
        except SecurityError:
            return
        # The window re-validates and loads the file; this widget never opens it itself.
        # Forwarded to MainWindow._route_asset_activation() -- the same
        # validated import path double-clicking an asset already uses.
        self.asset_dropped.emit(relative_path)
        event.acceptProposedAction()

    def import_image_as_layer(self, file_path: str) -> None:
        """Import an image as a new layer on the canvas and refresh the layers list."""
        self.canvas.import_image_as_layer(file_path)
        self.layers_panel.refresh()

    def get_state(self) -> dict:
        """Return the canvas state for project saving."""
        return self.canvas.get_state()

    def load_state(self, state: dict) -> None:
        """Restore the canvas and refresh the layers list."""
        self.canvas.load_state(state)
        self.layers_panel.refresh()
