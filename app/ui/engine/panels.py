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
        super().__init__(parent)
        self.workspace = workspace
        self.setHeaderLabel("Scene")
        self._refreshing = False
        self.itemSelectionChanged.connect(self._on_item_selection_changed)
        self.workspace.viewport.selection_changed.connect(self.select_node)
        self.workspace.engine_changed.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        """Rebuild the tree from the current scene graph."""
        selected_id = self.workspace.viewport.selected_node_id
        self._refreshing = True
        self.clear()
        for node in self.workspace.viewport.scene_graph.nodes:
            self.addTopLevelItem(self._item_for_node(node))
        self.expandAll()
        self._refreshing = False
        if selected_id is not None:
            self.select_node(selected_id)

    def select_node(self, node_id: str) -> None:
        """Select a tree item by node id."""
        if not node_id:
            self.clearSelection()
            return
        item = self._find_item(node_id)
        if item is not None:
            self._refreshing = True
            self.setCurrentItem(item)
            item.setSelected(True)
            self._refreshing = False

    def _item_for_node(self, node: SceneNode) -> QTreeWidgetItem:
        item = QTreeWidgetItem([node.display_name or node.node_id])
        item.setData(0, Qt.ItemDataRole.UserRole, node.node_id)
        for child in node.children:
            item.addChild(self._item_for_node(child))
        return item

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

    def _on_item_selection_changed(self) -> None:
        if self._refreshing:
            return
        item = self.currentItem()
        if item is None:
            self.workspace.viewport.select_node(None)
            return
        node_id = item.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(node_id, str):
            self.workspace.viewport.select_node(node_id)


class EnginePropertiesPanel(QWidget):
    """Live dock panel for editing the selected Engine scene node."""

    def __init__(self, workspace: object, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.workspace = workspace
        self._loading = False
        self._build_ui()
        self.workspace.viewport.selection_changed.connect(self.refresh)
        self.workspace.engine_changed.connect(self.refresh)
        self.refresh()

    def refresh(self, _node_id: str = "") -> None:
        """Load selected-node values into the panel controls."""
        node = self.workspace.viewport.selected_node()
        self._loading = True
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

    def apply_changes(self) -> None:
        """Apply panel values through the Engine workspace validation path."""
        if self._loading:
            return
        self.workspace.update_selected_node(
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
            collision_layer=int(self.layer_input.value()),
        )
        self.refresh()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name_input = QLineEdit()
        self.id_input = QLineEdit()
        self.shape_input = QComboBox()
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

    def _spin(self, minimum: float, maximum: float, decimals: int = 3) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setDecimals(decimals)
        return spin
