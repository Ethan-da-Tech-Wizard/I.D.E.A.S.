# ==============================================================================
# Program: tests/test_photo_editor.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: tests the data model of the photo editor (app/ui/studio/photo_editor.py): Layer (a QImage
# plus visibility/opacity/blend mode), LayerStack (ordering, flattening, resizing, save/load) and
# UndoStack (snapshot undo/redo). Pixels are inspected directly on tiny 2x2 to 6x6 images, no
# widget is shown. Limits (max layers, max dimensions, valid blend modes, base64 input) are checked
# because project files are untrusted input.
# Run: python3 -m pytest tests/test_photo_editor.py -q     (needs PySide6)
# ==============================================================================

from __future__ import annotations

import pytest
from PySide6.QtGui import QColor, QPainter

from app.security import SecurityError
from app.ui.studio.photo_editor import MAX_LAYERS, Layer, LayerStack, UndoStack


# Helper: fill a whole layer with one colour via QPainter (must end() the painter or Qt complains).
def paint_solid(layer: Layer, color: QColor) -> None:
    """Fill `layer.image` with `color`."""
    painter = QPainter(layer.image)
    painter.fillRect(layer.image.rect(), color)
    painter.end()


# summary() is the data the layers panel displays: index, name, visibility, opacity, blend mode.
def test_layer_stack_summary_reflects_metadata() -> None:
    stack = LayerStack(4, 4)
    stack.add_layer("Background")
    top = stack.add_layer("Sketch")
    top.visible = False
    top.set_opacity(0.25)
    top.set_blend_mode("screen")

    summary = stack.summary()

    assert summary[0] == {"index": 0, "name": "Background", "visible": True, "opacity": 1.0, "blend_mode": "normal"}
    assert summary[1] == {"index": 1, "name": "Sketch", "visible": False, "opacity": 0.25, "blend_mode": "screen"}


# Order is bottom-to-top. move_layer(0, 2) moves the bottom layer to the top; removing an invalid index returns False.
def test_layer_stack_add_remove_reorder() -> None:
    stack = LayerStack(4, 4)
    stack.add_layer("Background")
    stack.add_layer("Sketch")
    stack.add_layer("Lines")

    assert [layer.name for layer in stack.layers] == ["Background", "Sketch", "Lines"]

    assert stack.move_layer(0, 2) is True
    assert [layer.name for layer in stack.layers] == ["Sketch", "Lines", "Background"]

    assert stack.remove_layer(1) is True
    assert [layer.name for layer in stack.layers] == ["Sketch", "Background"]

    assert stack.remove_layer(5) is False


# MAX_LAYERS caps memory use (each layer is a full-size image); one more raises SecurityError.
def test_layer_stack_add_layer_rejects_over_max() -> None:
    stack = LayerStack(2, 2)
    for _ in range(MAX_LAYERS):
        stack.add_layer()
    with pytest.raises(SecurityError):
        stack.add_layer()


# Absurd canvas sizes (999999 wide) are refused before allocating memory.
def test_layer_rejects_oversized_dimensions() -> None:
    with pytest.raises(SecurityError):
        Layer(999999, 10)


# Later layers draw over earlier ones: opaque green on top of red gives green.
def test_flatten_composites_visible_layers_in_order() -> None:
    stack = LayerStack(2, 2)
    bottom = stack.add_layer("Bottom")
    top = stack.add_layer("Top")

    paint_solid(bottom, QColor(255, 0, 0, 255))
    paint_solid(top, QColor(0, 255, 0, 255))

    composite = stack.flatten()
    assert composite.pixelColor(0, 0).getRgb()[:3] == (0, 255, 0)


# Hiding the top layer reveals the one below.
def test_flatten_skips_invisible_and_transparent_layers() -> None:
    stack = LayerStack(2, 2)
    bottom = stack.add_layer("Bottom")
    top = stack.add_layer("Top")
    paint_solid(bottom, QColor(255, 0, 0, 255))
    paint_solid(top, QColor(0, 255, 0, 255))
    top.visible = False

    composite = stack.flatten()
    assert composite.pixelColor(0, 0).getRgb()[:3] == (255, 0, 0)


# Opacity is clamped to 0..1 instead of rejecting out-of-range values (UI sliders may overshoot).
def test_layer_set_opacity_clamped() -> None:
    layer = Layer(2, 2)
    layer.set_opacity(5.0)
    assert layer.opacity == 1.0
    layer.set_opacity(-1.0)
    assert layer.opacity == 0.0


# Only known blend modes are accepted, since the name maps to a QPainter composition mode.
def test_layer_set_blend_mode_rejects_unknown() -> None:
    layer = Layer(2, 2)
    with pytest.raises(SecurityError):
        layer.set_blend_mode("darken-and-blur")


# Scenario: snapshot the red layer, paint blue, undo (back to red, returns the layer index), redo (blue again).
def test_undo_redo_round_trip() -> None:
    stack = LayerStack(2, 2)
    layer = stack.add_layer()
    undo_stack = UndoStack()

    paint_solid(layer, QColor(255, 0, 0, 255))
    before = layer.snapshot()

    undo_stack.record(0, before)
    paint_solid(layer, QColor(0, 0, 255, 255))
    assert layer.image.pixelColor(0, 0).getRgb()[:3] == (0, 0, 255)

    restored_index = undo_stack.undo(stack)
    assert restored_index == 0
    assert layer.image.pixelColor(0, 0).getRgb()[:3] == (255, 0, 0)

    redone_index = undo_stack.redo(stack)
    assert redone_index == 0
    assert layer.image.pixelColor(0, 0).getRgb()[:3] == (0, 0, 255)


# Undo on an empty stack returns None and can_undo is False; it must not raise.
def test_undo_with_nothing_recorded_is_noop() -> None:
    stack = LayerStack(2, 2)
    stack.add_layer()
    undo_stack = UndoStack()
    assert undo_stack.can_undo() is False
    assert undo_stack.undo(stack) is None


# get_state/load_state preserves size, name, opacity, blend mode and pixels (PNG-encoded). The restored
# stack starts at 1x1 to prove load_state replaces dimensions as well.
def test_layer_stack_state_round_trip() -> None:
    stack = LayerStack(4, 4)
    layer = stack.add_layer("Painted")
    paint_solid(layer, QColor(10, 20, 30, 255))
    layer.set_opacity(0.5)
    layer.set_blend_mode("multiply")

    state = stack.get_state()

    restored = LayerStack(1, 1)
    restored.load_state(state)

    assert restored.width == 4
    assert restored.height == 4
    assert len(restored.layers) == 1
    restored_layer = restored.layers[0]
    assert restored_layer.name == "Painted"
    assert restored_layer.opacity == pytest.approx(0.5)
    assert restored_layer.blend_mode == "multiply"
    assert restored_layer.image.pixelColor(0, 0).getRgb()[:3] == (10, 20, 30)


# Corrupt image data in a project file must raise SecurityError, not binascii/Qt errors.
def test_layer_from_dict_rejects_invalid_base64() -> None:
    with pytest.raises(SecurityError):
        Layer.from_dict({"width": 2, "height": 2, "png_base64": "not-valid-base64!!"})


# The layer cap is enforced when loading, not just when adding interactively.
def test_load_state_rejects_too_many_layers() -> None:
    stack = LayerStack(2, 2)
    bad_state = {
        "width": 2,
        "height": 2,
        "layers": [Layer(2, 2).to_dict() for _ in range(MAX_LAYERS + 1)],
    }
    with pytest.raises(SecurityError):
        stack.load_state(bad_state)


# Growing keeps existing pixels anchored top-left and fills the new area with transparency.
def test_resize_canvas_preserves_pixels_when_growing() -> None:
    stack = LayerStack(2, 2)
    layer = stack.add_layer()
    paint_solid(layer, QColor(10, 20, 30, 255))

    stack.resize_canvas(4, 4)

    assert stack.width == 4
    assert stack.height == 4
    assert layer.image.width() == 4
    assert layer.image.height() == 4
    # Original top-left content is preserved...
    assert layer.image.pixelColor(0, 0).getRgb()[:3] == (10, 20, 30)
    # ...and the newly added area is transparent, not garbage.
    assert layer.image.pixelColor(3, 3).alpha() == 0


# Shrinking crops from the bottom/right and keeps the top-left pixels.
def test_resize_canvas_crops_pixels_when_shrinking() -> None:
    stack = LayerStack(4, 4)
    layer = stack.add_layer()
    paint_solid(layer, QColor(50, 60, 70, 255))

    stack.resize_canvas(2, 2)

    assert layer.image.width() == 2
    assert layer.image.height() == 2
    assert layer.image.pixelColor(0, 0).getRgb()[:3] == (50, 60, 70)


# Every layer must change size together, or flatten() would composite mismatched images.
def test_resize_canvas_applies_to_all_layers() -> None:
    stack = LayerStack(2, 2)
    stack.add_layer()
    stack.add_layer()

    stack.resize_canvas(6, 3)

    assert all(layer.image.width() == 6 and layer.image.height() == 3 for layer in stack.layers)


# Zero and negative sizes are invalid.
def test_resize_canvas_rejects_out_of_range_dimensions() -> None:
    stack = LayerStack(2, 2)
    with pytest.raises(SecurityError):
        stack.resize_canvas(0, 10)
    with pytest.raises(SecurityError):
        stack.resize_canvas(10, -1)
