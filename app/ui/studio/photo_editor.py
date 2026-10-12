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

MAX_LAYERS = 64
MAX_UNDO_DEPTH = 50
MAX_CANVAS_DIMENSION = 8192

BLEND_MODES = ("normal", "multiply", "screen")

_COMPOSITION_MODE_BY_BLEND = {
    "normal": QPainter.CompositionMode.CompositionMode_SourceOver,
    "multiply": QPainter.CompositionMode.CompositionMode_Multiply,
    "screen": QPainter.CompositionMode.CompositionMode_Screen,
}


class ToolKind(str, Enum):
    BRUSH = "brush"
    ERASER = "eraser"
    RECTANGLE = "rectangle"
    ELLIPSE = "ellipse"
    LINE = "line"


class Layer:
    """A single raster layer: a QImage buffer plus display metadata."""

    def __init__(self, width: int, height: int, name: str = "Layer") -> None:
        if not (0 < width <= MAX_CANVAS_DIMENSION and 0 < height <= MAX_CANVAS_DIMENSION):
            raise SecurityError("Layer dimensions are out of range.")
        self.name = name
        self.opacity = 1.0
        self.visible = True
        self.blend_mode = "normal"
        self.image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
        self.image.fill(QColor(0, 0, 0, 0))

    def set_opacity(self, opacity: float) -> None:
        self.opacity = min(1.0, max(0.0, opacity))

    def set_blend_mode(self, blend_mode: str) -> None:
        if blend_mode not in BLEND_MODES:
            raise SecurityError("Unsupported blend mode.")
        self.blend_mode = blend_mode

    def snapshot(self) -> QImage:
        return self.image.copy()

    def restore(self, snapshot: QImage) -> None:
        self.image = snapshot.copy()

    def to_dict(self) -> dict:
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
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
        width = int(data.get("width", 1))
        height = int(data.get("height", 1))
        layer = cls(width, height, name=str(data.get("name", "Layer")))
        layer.set_opacity(float(data.get("opacity", 1.0)))
        layer.visible = bool(data.get("visible", True))
        layer.set_blend_mode(str(data.get("blend_mode", "normal")))
        encoded = data.get("png_base64")
        if encoded:
            try:
                raw = base64.b64decode(encoded, validate=True)
            except Exception as exc:  # binascii.Error subclasses ValueError
                raise SecurityError("Layer image data is not valid base64.") from exc
            image = QImage()
            if not image.loadFromData(QByteArray(raw), "PNG"):
                raise SecurityError("Layer image data is not a valid PNG.")
            layer.image = image
        return layer


class LayerStack:
    """Ordered stack of layers, bottom (index 0) to top."""

    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height
        self.layers: list[Layer] = []

    def add_layer(self, name: str = "") -> Layer:
        if len(self.layers) >= MAX_LAYERS:
            raise SecurityError("Too many layers.")
        layer = Layer(self.width, self.height, name=name or f"Layer {len(self.layers) + 1}")
        self.layers.append(layer)
        return layer

    def remove_layer(self, index: int) -> bool:
        if not (0 <= index < len(self.layers)):
            return False
        del self.layers[index]
        return True

    def move_layer(self, from_index: int, to_index: int) -> bool:
        if not (0 <= from_index < len(self.layers)) or not (0 <= to_index < len(self.layers)):
            return False
        layer = self.layers.pop(from_index)
        self.layers.insert(to_index, layer)
        return True

    def resize_canvas(self, width: int, height: int) -> None:
        """Reallocates every layer's QImage at the new size, preserving
        existing pixel content anchored at the top-left -- cropping if the
        new size is smaller, padding with transparency if larger."""
        if not (0 < width <= MAX_CANVAS_DIMENSION and 0 < height <= MAX_CANVAS_DIMENSION):
            raise SecurityError("Canvas dimensions are out of range.")
        for layer in self.layers:
            resized = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
            resized.fill(QColor(0, 0, 0, 0))
            painter = QPainter(resized)
            painter.drawImage(0, 0, layer.image)
            painter.end()
            layer.image = resized
        self.width = width
        self.height = height

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
        composite = QImage(self.width, self.height, QImage.Format.Format_ARGB32_Premultiplied)
        composite.fill(QColor(0, 0, 0, 0))
        painter = QPainter(composite)
        for layer in self.layers:
            if not layer.visible or layer.opacity <= 0.0:
                continue
            painter.setOpacity(layer.opacity)
            painter.setCompositionMode(_COMPOSITION_MODE_BY_BLEND[layer.blend_mode])
            painter.drawImage(0, 0, layer.image)
        painter.end()
        return composite

    def get_state(self) -> dict:
        return {
            "width": self.width,
            "height": self.height,
            "layers": [layer.to_dict() for layer in self.layers],
        }

    def load_state(self, state: dict) -> None:
        layers_data = state.get("layers", [])
        if len(layers_data) > MAX_LAYERS:
            raise SecurityError("Too many layers in saved state.")
        self.width = int(state.get("width", self.width))
        self.height = int(state.get("height", self.height))
        self.layers = [Layer.from_dict(data) for data in layers_data]


class UndoStack:
    """Records pre-change QImage snapshots for a layer stack, for undo/redo."""

    def __init__(self, max_depth: int = MAX_UNDO_DEPTH) -> None:
        self.max_depth = max_depth
        self._undo: list[tuple[int, QImage]] = []
        self._redo: list[tuple[int, QImage]] = []

    def record(self, layer_index: int, snapshot: QImage) -> None:
        self._undo.append((layer_index, snapshot))
        if len(self._undo) > self.max_depth:
            self._undo.pop(0)
        self._redo.clear()

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo(self, layer_stack: LayerStack) -> Optional[int]:
        if not self._undo:
            return None
        layer_index, snapshot = self._undo.pop()
        if not (0 <= layer_index < len(layer_stack.layers)):
            return None
        layer = layer_stack.layers[layer_index]
        self._redo.append((layer_index, layer.snapshot()))
        layer.restore(snapshot)
        return layer_index

    def redo(self, layer_stack: LayerStack) -> Optional[int]:
        if not self._redo:
            return None
        layer_index, snapshot = self._redo.pop()
        if not (0 <= layer_index < len(layer_stack.layers)):
            return None
        layer = layer_stack.layers[layer_index]
        self._undo.append((layer_index, layer.snapshot()))
        layer.restore(snapshot)
        return layer_index


MIN_CANVAS_ZOOM = 0.1
MAX_CANVAS_ZOOM = 8.0


class PhotoEditorWidget(QWidget):
    """The main raster canvas: renders the flattened layer stack and hosts tools."""

    layer_changed = Signal(int)

    def __init__(self, parent: QWidget | None = None, width: int = 1024, height: int = 768) -> None:
        super().__init__(parent)
        self.setObjectName("PhotoEditorWidget")
        self.setMinimumSize(200, 200)
        self.layer_stack = LayerStack(width, height)
        self.layer_stack.add_layer("Background")
        self.undo_stack = UndoStack()
        self.active_layer_index = 0
        self.active_tool = ToolKind.BRUSH
        self.brush_size = 8
        self.brush_color = QColor("#ffffff")
        self._drawing = False
        self._last_point: Optional[QPoint] = None
        self._shape_start: Optional[QPoint] = None
        self._pre_stroke_snapshot: Optional[QImage] = None

        self._zoom_level = 1.0
        self._view_offset = QPointF(0.0, 0.0)
        self._panning = False
        self._pan_last_pos: Optional[QPoint] = None

        undo_shortcut = QShortcut(QKeySequence.StandardKey.Undo, self)
        undo_shortcut.activated.connect(self.undo)
        redo_shortcut = QShortcut(QKeySequence.StandardKey.Redo, self)
        redo_shortcut.activated.connect(self.redo)

    def _current_transform(self) -> QTransform:
        transform = QTransform()
        transform.translate(self._view_offset.x(), self._view_offset.y())
        transform.scale(self._zoom_level, self._zoom_level)
        return transform

    def _screen_to_image(self, pos: QPointF) -> QPoint:
        inverted, invertible = self._current_transform().inverted()
        if not invertible:
            return QPoint(int(pos.x()), int(pos.y()))
        mapped = inverted.map(QPointF(pos))
        return QPoint(int(mapped.x()), int(mapped.y()))

    def active_layer(self) -> Optional[Layer]:
        if 0 <= self.active_layer_index < len(self.layer_stack.layers):
            return self.layer_stack.layers[self.active_layer_index]
        return None

    def set_active_tool(self, tool: ToolKind) -> None:
        self.active_tool = tool

    def choose_color(self) -> None:
        color = QColorDialog.getColor(self.brush_color, self, "Choose Drawing Color")
        if color.isValid():
            self.brush_color = color

    def _begin_stroke(self, pos: QPoint) -> None:
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
        layer = self.active_layer()
        if layer is None or self._shape_start is None:
            return
        painter = QPainter(layer.image)
        painter.setPen(QPen(self.brush_color, self.brush_size))
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
        if event.button() == Qt.MouseButton.LeftButton:
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                self._panning = True
                self._pan_last_pos = event.position().toPoint()
                return
            self._begin_stroke(self._screen_to_image(event.position()))

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt override
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
        delta = event.angleDelta().y()
        factor = 1.1 if delta > 0 else 0.9
        self._zoom_level = min(MAX_CANVAS_ZOOM, max(MIN_CANVAS_ZOOM, self._zoom_level * factor))
        self.update()

    def undo(self) -> None:
        if self.undo_stack.undo(self.layer_stack) is not None:
            self.update()

    def redo(self) -> None:
        if self.undo_stack.redo(self.layer_stack) is not None:
            self.update()

    def resize_canvas(self, width: int, height: int) -> None:
        self.layer_stack.resize_canvas(width, height)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setTransform(self._current_transform())
        painter.drawImage(QPointF(0, 0), self.layer_stack.flatten())
        painter.end()

    def get_state(self) -> dict:
        return self.layer_stack.get_state()

    def load_state(self, state: dict) -> None:
        self.layer_stack.load_state(state)
        self.active_layer_index = 0
        self.undo_stack = UndoStack()
        self.update()

    def import_image_as_layer(self, file_path: str) -> None:
        """Loads an external image file as a new top layer, scaled to canvas size.

        Used by the asset-manager wiring (double-click/drag an image asset).
        Path trust follows the same convention as MainWindow.open_file(): the
        path comes from a user-driven selection (dialog or catalogued asset),
        and Qt's image codecs -- not arbitrary code -- do the loading.
        """
        image = QImage(file_path)
        if image.isNull():
            raise SecurityError("Could not load image file.")
        layer = self.layer_stack.add_layer(name=os.path.basename(file_path))
        painter = QPainter(layer.image)
        painter.drawImage(layer.image.rect(), image, image.rect())
        painter.end()
        self.active_layer_index = len(self.layer_stack.layers) - 1
        self.update()
        self.layer_changed.emit(self.active_layer_index)


class PhotoEditorToolbar(QWidget):
    """Tool selection, brush size, color swatch, and undo/redo controls."""

    def __init__(self, editor: PhotoEditorWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("PhotoEditorToolbar")
        self.editor = editor
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        self._tool_buttons: dict[ToolKind, QPushButton] = {}
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
        self.editor.set_active_tool(tool)
        for candidate, button in self._tool_buttons.items():
            button.setChecked(candidate == tool)

    def _on_size_changed(self, value: int) -> None:
        self.editor.brush_size = value

    def _update_swatch_color(self) -> None:
        self.color_swatch.setStyleSheet(f"background-color: {self.editor.brush_color.name()};")

    def _on_choose_color(self) -> None:
        self.editor.choose_color()
        self._update_swatch_color()


class LayersPanel(QWidget):
    """Lists a PhotoEditorWidget's layers with visibility/opacity controls and reordering."""

    def __init__(self, editor: PhotoEditorWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("LayersPanel")
        self.editor = editor
        self.setup_ui()
        self.refresh()

    def setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addWidget(QLabel("Layers"))

        self.list_widget = QListWidget()
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
        layer = self.editor.active_layer()
        self.visible_checkbox.blockSignals(True)
        self.opacity_slider.blockSignals(True)
        if layer is not None:
            self.visible_checkbox.setChecked(layer.visible)
            self.opacity_slider.setValue(int(layer.opacity * 100))
        self.visible_checkbox.blockSignals(False)
        self.opacity_slider.blockSignals(False)

    def _on_row_selected(self, row: int) -> None:
        if row < 0:
            return
        self.editor.active_layer_index = row
        self._sync_controls()

    def _on_visibility_toggled(self, checked: bool) -> None:
        layer = self.editor.active_layer()
        if layer is not None:
            layer.visible = checked
            self.editor.update()
            self.refresh()

    def _on_opacity_changed(self, value: int) -> None:
        layer = self.editor.active_layer()
        if layer is not None:
            layer.set_opacity(value / 100.0)
            self.editor.update()

    def _on_add_layer(self) -> None:
        self.editor.layer_stack.add_layer()
        self.editor.active_layer_index = len(self.editor.layer_stack.layers) - 1
        self.editor.update()
        self.refresh()

    def _on_remove_layer(self) -> None:
        if len(self.editor.layer_stack.layers) <= 1:
            return
        self.editor.layer_stack.remove_layer(self.editor.active_layer_index)
        self.editor.active_layer_index = max(0, self.editor.active_layer_index - 1)
        self.editor.update()
        self.refresh()

    def _on_move_up(self) -> None:
        index = self.editor.active_layer_index
        if index > 0:
            self.editor.layer_stack.move_layer(index, index - 1)
            self.editor.active_layer_index = index - 1
            self.editor.update()
            self.refresh()

    def _on_move_down(self) -> None:
        index = self.editor.active_layer_index
        if index < len(self.editor.layer_stack.layers) - 1:
            self.editor.layer_stack.move_layer(index, index + 1)
            self.editor.active_layer_index = index + 1
            self.editor.update()
            self.refresh()


class PhotoEditorPanel(QWidget):
    """Complete photo editor surface: toolbar + canvas + layers panel, composed together."""

    asset_dropped = Signal(str)

    def __init__(self, parent: QWidget | None = None, width: int = 1024, height: int = 768) -> None:
        super().__init__(parent)
        self.setObjectName("PhotoEditorPanel")
        self.canvas = PhotoEditorWidget(width=width, height=height)
        self.toolbar = PhotoEditorToolbar(self.canvas)
        self.layers_panel = LayersPanel(self.canvas)
        self.canvas.layer_changed.connect(lambda _index: self.layers_panel.refresh())
        self.setAcceptDrops(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.toolbar)

        body = QHBoxLayout()
        body.addWidget(self.canvas, 1)
        body.addWidget(self.layers_panel)
        layout.addLayout(body)

    def dragEnterEvent(self, event) -> None:  # noqa: N802 - Qt override
        from app.ui.assets.asset_manager import ASSET_MIME_TYPE

        if event.mimeData().hasFormat(ASSET_MIME_TYPE):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802 - Qt override
        from app.ui.assets.asset_manager import ASSET_MIME_TYPE, decode_asset_mime_payload

        mime = event.mimeData()
        if not mime.hasFormat(ASSET_MIME_TYPE):
            return
        try:
            relative_path = decode_asset_mime_payload(bytes(mime.data(ASSET_MIME_TYPE)))
        except SecurityError:
            return
        # Forwarded to MainWindow._route_asset_activation() -- the same
        # validated import path double-clicking an asset already uses.
        self.asset_dropped.emit(relative_path)
        event.acceptProposedAction()

    def import_image_as_layer(self, file_path: str) -> None:
        self.canvas.import_image_as_layer(file_path)
        self.layers_panel.refresh()

    def get_state(self) -> dict:
        return self.canvas.get_state()

    def load_state(self, state: dict) -> None:
        self.canvas.load_state(state)
        self.layers_panel.refresh()
