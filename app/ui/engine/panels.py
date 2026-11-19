# ==============================================================================
# Program: app/ui/engine/panels.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: two dockable Qt panels bound to an EngineWorkspace: EngineSceneTreePanel (hierarchy view
# that stays in sync with the viewport selection) and EnginePropertiesPanel (form for editing the
# selected node's transform, shape and physics fields). They contain no scene logic: edits go through
# workspace.update_selected_node and the viewport, so validation and undo history stay in one place.
# Used by: EngineWorkspace.dock_panel_factories() in workspace.py, docked by PanelManager
# (app/ui/themes/dock_panels.py); tests/test_engine_panels.py.
# Wiring: both panels listen to viewport.selection_changed and workspace.engine_changed.
# Re-entrancy guards (_refreshing, _loading) stop programmatic updates from echoing back as user
# edits. The `workspace` argument is typed `object` to avoid a circular import with workspace.py.
# ==============================================================================

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QLineEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.ui.engine.viewport import ALLOWED_SHAPES, SceneNode


class EngineSceneTreePanel(QTreeWidget):
    """Live dock panel for selecting nodes from the Engine scene graph."""

    def __init__(self, workspace: object, parent: QWidget | None = None) -> None:
        """Build the tree and subscribe to workspace/viewport signals.

        Args:
            workspace: the EngineWorkspace to mirror (expects .viewport, .engine_changed).
            parent: optional Qt parent.
        """
        super().__init__(parent)
        self.workspace = workspace
        self.setHeaderLabel("Scene")
        # True while the tree is being changed programmatically, so selection signals are ignored.
        self._refreshing = False
        # User clicks in the tree -> select that node in the viewport.
        self.itemSelectionChanged.connect(self._on_item_selection_changed)
        # Selection made in the viewport -> highlight in the tree. engine_changed -> rebuild the tree.
        self.workspace.viewport.selection_changed.connect(self.select_node)
        self.workspace.engine_changed.connect(self.refresh)
        self.refresh()

    # Rebuild the tree from the current scene graph.
    def refresh(self) -> None:
        """Rebuild the tree from the current scene graph."""
        # Remember the selection: clear() would drop it.
        selected_id = self.workspace.viewport.selected_node_id
        self._refreshing = True
        self.clear()
        for node in self.workspace.viewport.scene_graph.nodes:
            self.addTopLevelItem(self._item_for_node(node))
        # Show the whole hierarchy; scenes are small.
        self.expandAll()
        # Restored before re-selecting so select_node is not blocked by the guard.
        self._refreshing = False
        if selected_id is not None:
            self.select_node(selected_id)

    # Select a tree item by node id (an empty id clears the selection).
    def select_node(self, node_id: str) -> None:
        """Select a tree item by node id."""
        if not node_id:
            self.clearSelection()
            return
        item = self._find_item(node_id)
        if item is not None:
            # Guard: setCurrentItem emits itemSelectionChanged, which would call back into the viewport.
            self._refreshing = True
            self.setCurrentItem(item)
            item.setSelected(True)
            self._refreshing = False

    # Build the item for one node and, recursively, its children. The node id is stored in UserRole so the display name can differ from the id.
    def _item_for_node(self, node: SceneNode) -> QTreeWidgetItem:
        item = QTreeWidgetItem([node.display_name or node.node_id])
        item.setData(0, Qt.ItemDataRole.UserRole, node.node_id)
        for child in node.children:
            item.addChild(self._item_for_node(child))
        return item

    # Iterative depth-first search for the item holding `node_id` (a stack instead of recursion).
    def _find_item(self, node_id: str) -> QTreeWidgetItem | None:
        stack = [self.topLevelItem(index) for index in range(self.topLevelItemCount())]
        while stack:
            item = stack.pop()
            if item is None:
                continue
            if item.data(0, Qt.ItemDataRole.UserRole) == node_id:
                return item
            stack.extend(item.child(index) for index in range(item.childCount()))
        return None

    # Forward a user selection to the viewport, ignoring programmatic changes.
    def _on_item_selection_changed(self) -> None:
        # Ignore changes made by refresh()/select_node().
        if self._refreshing:
            return
        item = self.currentItem()
        if item is None:
            self.workspace.viewport.select_node(None)
            return
        node_id = item.data(0, Qt.ItemDataRole.UserRole)
        # Items without a node id (should not happen) are ignored.
        if isinstance(node_id, str):
            self.workspace.viewport.select_node(node_id)


class EnginePropertiesPanel(QWidget):
    """Live dock panel for editing the selected Engine scene node."""

    def __init__(self, workspace: object, parent: QWidget | None = None) -> None:
        """Build the form and subscribe to selection and change signals.

        Args:
            workspace: the EngineWorkspace being edited.
            parent: optional Qt parent.
        """
        super().__init__(parent)
        self.workspace = workspace
        # True while the panel fills its controls from a node, so apply_changes does nothing during that.
        self._loading = False
        self._build_ui()
        self.workspace.viewport.selection_changed.connect(self.refresh)
        self.workspace.engine_changed.connect(self.refresh)
        self.refresh()

    # Load selected-node values into the panel controls.
    #
    # Args:
    #     _node_id: unused; present so the method can be connected to selection_changed(str).
    def refresh(self, _node_id: str = "") -> None:
        """Load selected-node values into the panel controls."""
        node = self.workspace.viewport.selected_node()
        self._loading = True
        # Disable the whole form when nothing is selected.
        self.setEnabled(node is not None)
        if node is not None:
            self.name_input.setText(node.display_name or node.node_id)
            self.id_input.setText(node.node_id)
            self.shape_input.setCurrentText(node.shape)
            self.color_input.setText(node.color)
            self.x_input.setValue(node.x)
            self.y_input.setValue(node.y)
            self.width_input.setValue(node.width)
            self.height_input.setValue(node.height)
            self.vx_input.setValue(node.velocity_x)
            self.vy_input.setValue(node.velocity_y)
            self.mass_input.setValue(node.mass)
            # Collider size falls back to the visual size when the node has no explicit collider.
            self.collider_width_input.setValue(node.collider_width if node.collider_width is not None else node.width)
            self.collider_height_input.setValue(node.collider_height if node.collider_height is not None else node.height)
            self.static_input.setChecked(node.is_static)
            self.trigger_input.setChecked(node.is_trigger)
            self.restitution_input.setValue(node.restitution)
            self.friction_input.setValue(node.friction)
            self.damping_input.setValue(node.damping)
            self.gravity_scale_input.setValue(node.gravity_scale)
            self.layer_input.setValue(node.collision_layer)
        self._loading = False

    # Send all form values to the workspace in one update, then reload the form.
    #
    # Does nothing while the panel is loading. The workspace validates (SecurityError on bad values) and records undo history.
    def apply_changes(self) -> None:
        """Apply panel values through the Engine workspace validation path."""
        if self._loading:
            return
        self.workspace.update_selected_node(
            # The id is editable; the workspace is responsible for rejecting duplicates.
            node_id=self.id_input.text().strip(),
            display_name=self.name_input.text().strip(),
            shape=self.shape_input.currentText(),
            color=self.color_input.text().strip(),
            x=self.x_input.value(),
            y=self.y_input.value(),
            width=self.width_input.value(),
            height=self.height_input.value(),
            velocity_x=self.vx_input.value(),
            velocity_y=self.vy_input.value(),
            mass=self.mass_input.value(),
            collider_width=self.collider_width_input.value(),
            collider_height=self.collider_height_input.value(),
            is_static=self.static_input.isChecked(),
            is_trigger=self.trigger_input.isChecked(),
            restitution=self.restitution_input.value(),
            friction=self.friction_input.value(),
            damping=self.damping_input.value(),
            gravity_scale=self.gravity_scale_input.value(),
            # The layer spin box is a double box with 0 decimals, so convert to int.
            collision_layer=int(self.layer_input.value()),
        )
        # Re-read the node so the form shows exactly what was accepted (for example clamped values).
        self.refresh()

    # Create the controls: one row per editable field in a form layout, then an Apply button. Spin box ranges mirror the limits enforced by SceneNode in viewport.py (for example restitution/friction 0..1).
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name_input = QLineEdit()
        self.id_input = QLineEdit()
        self.shape_input = QComboBox()
        # Choices come from the same ALLOWED_SHAPES set the viewport validates against.
        self.shape_input.addItems(sorted(ALLOWED_SHAPES))
        self.color_input = QLineEdit()
        self.x_input = self._spin(-100000.0, 100000.0)
        self.y_input = self._spin(-100000.0, 100000.0)
        self.width_input = self._spin(0.0, 100000.0)
        self.height_input = self._spin(0.0, 100000.0)
        self.vx_input = self._spin(-10000.0, 10000.0)
        self.vy_input = self._spin(-10000.0, 10000.0)
        self.mass_input = self._spin(0.001, 100000.0)
        self.collider_width_input = self._spin(0.0, 100000.0)
        self.collider_height_input = self._spin(0.0, 100000.0)
        self.static_input = QCheckBox()
        self.trigger_input = QCheckBox()
        self.restitution_input = self._spin(0.0, 1.0)
        self.friction_input = self._spin(0.0, 1.0)
        self.damping_input = self._spin(0.0, 10.0)
        self.gravity_scale_input = self._spin(-10.0, 10.0)
        self.layer_input = self._spin(0.0, 64.0, decimals=0)
        # Label/widget pairs in display order.
        for label, widget in (
            ("Name", self.name_input),
            ("ID", self.id_input),
            ("Shape", self.shape_input),
            ("Color", self.color_input),
            ("X", self.x_input),
            ("Y", self.y_input),
            ("Width", self.width_input),
            ("Height", self.height_input),
            ("Velocity X", self.vx_input),
            ("Velocity Y", self.vy_input),
            ("Mass", self.mass_input),
            ("Collider W", self.collider_width_input),
            ("Collider H", self.collider_height_input),
            ("Static", self.static_input),
            ("Trigger", self.trigger_input),
            ("Restitution", self.restitution_input),
            ("Friction", self.friction_input),
            ("Damping", self.damping_input),
            ("Gravity Scale", self.gravity_scale_input),
            ("Layer", self.layer_input),
        ):
            form.addRow(label, widget)
        layout.addLayout(form)
        apply_button = QPushButton("Apply")
        apply_button.clicked.connect(self.apply_changes)
        layout.addWidget(apply_button)

    # Create a QDoubleSpinBox with the given range and number of decimals (3 by default; 0 for integer-like fields).
    def _spin(self, minimum: float, maximum: float, decimals: int = 3) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setDecimals(decimals)
        return spin
