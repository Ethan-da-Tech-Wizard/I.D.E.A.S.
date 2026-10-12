from __future__ import annotations

from typing import Any

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtGui import QKeySequence, QShortcut

from app.security import SecurityError
from app.ui.engine.ecs import Collider, RigidBody, Transform, World
from app.ui.engine.history import EngineHistory
from app.ui.engine.physics import PhysicsSystem
from app.ui.engine.scene_io import export_scene_file, import_scene_file
from app.ui.engine.shader_builder import ShaderBuilderWidget
from app.ui.engine.viewport import ALLOWED_SHAPES, SceneNode, ViewportWidget


class EngineWorkspace(QWidget):
    """Engine mode wrapper combining the viewport and shader builder."""

    engine_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("EngineWorkspace")
        self.viewport = ViewportWidget()
        self.shader_builder = ShaderBuilderWidget()
        self.world = World()
        self.physics = PhysicsSystem()
        self.history = EngineHistory()
        self._node_entities: dict[str, int] = {}
        self._edit_baseline: dict[str, Any] | None = None
        self._primitive_counter = 0
        self._simulation_running = False
        self._physics_timer = QTimer(self)
        self._physics_timer.setInterval(16)
        self._physics_timer.timeout.connect(self.step_physics)
        self._loading_inspector = False
        self.setup_ui()
        self._seed_default_scene()
        self.viewport.selection_changed.connect(self.refresh_inspector)
        self._install_shortcuts()

    def setup_ui(self) -> None:
        """Build engine workspace toolbar and tabs."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)
        title = QLabel("ENGINE")
        title.setObjectName("panelTitle")
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        for label, shape in (("Rectangle", "rectangle"), ("Circle", "circle"), ("Line", "line"), ("Polygon", "polygon")):
            button = QPushButton(label)
            button.clicked.connect(lambda _checked=False, primitive=shape: self.create_scene_node(primitive))
            toolbar.addWidget(button)

        self.grid_toggle = QCheckBox("Grid")
        self.grid_toggle.setChecked(True)
        self.grid_toggle.toggled.connect(self._apply_grid_controls)
        self.snap_toggle = QCheckBox("Snap")
        self.snap_toggle.toggled.connect(self._apply_grid_controls)
        self.grid_size = QDoubleSpinBox()
        self.grid_size.setRange(5.0, 500.0)
        self.grid_size.setValue(25.0)
        self.grid_size.valueChanged.connect(self._apply_grid_controls)
        toolbar.addWidget(self.grid_toggle)
        toolbar.addWidget(self.snap_toggle)
        toolbar.addWidget(QLabel("Grid Size"))
        toolbar.addWidget(self.grid_size)

        reset_camera = QPushButton("Reset Camera")
        reset_camera.clicked.connect(self.viewport.reset_camera)
        zoom_fit = QPushButton("Zoom Fit")
        zoom_fit.clicked.connect(self.viewport.zoom_to_fit)
        toolbar.addWidget(reset_camera)
        toolbar.addWidget(zoom_fit)

        self.gravity_input = QDoubleSpinBox()
        self.gravity_input.setRange(-5000.0, 5000.0)
        self.gravity_input.setValue(self.physics.gravity)
        self.gravity_input.valueChanged.connect(self._apply_physics_controls)
        self.floor_input = QDoubleSpinBox()
        self.floor_input.setRange(50.0, 10000.0)
        self.floor_input.setValue(self.physics.floor_y)
        self.floor_input.valueChanged.connect(self._apply_physics_controls)
        toolbar.addWidget(QLabel("Gravity"))
        toolbar.addWidget(self.gravity_input)
        toolbar.addWidget(QLabel("Floor"))
        toolbar.addWidget(self.floor_input)

        play_button = QPushButton("Play")
        play_button.clicked.connect(self.play_physics)
        pause_button = QPushButton("Pause")
        pause_button.clicked.connect(self.pause_physics)
        reset_button = QPushButton("Reset")
        reset_button.clicked.connect(self.reset_physics)
        toolbar.addWidget(play_button)
        toolbar.addWidget(pause_button)
        toolbar.addWidget(reset_button)
        toolbar.addStretch()

        self.tabs = QTabWidget()
        self.tabs.addTab(self.viewport, "Viewport")
        self.tabs.addTab(self.shader_builder, "Shader Builder")
        self.inspector = self._build_inspector()
        self.tabs.addTab(self.inspector, "Inspector")
        layout.addWidget(title)
        layout.addLayout(toolbar)
        layout.addWidget(self.tabs, 1)

    def get_state(self) -> dict[str, Any]:
        """Serialize engine workspace state."""
        return {
            "viewport": self.viewport.get_state(),
            "shader_builder": self.shader_builder.get_state(),
            "physics": {
                "gravity": self.physics.gravity,
                "floor_y": self.physics.floor_y,
                "world_width": self.physics.world_width,
            },
        }

    def load_state(self, state: dict[str, Any]) -> None:
        """Restore engine workspace state."""
        if "viewport" in state:
            self.viewport.load_state(state["viewport"])
        if "shader_builder" in state:
            self.shader_builder.load_state(state["shader_builder"])
        physics_state = state.get("physics", {})
        if isinstance(physics_state, dict):
            self.physics.gravity = float(physics_state.get("gravity", self.physics.gravity))
            self.physics.floor_y = float(physics_state.get("floor_y", self.physics.floor_y))
            self.physics.world_width = float(physics_state.get("world_width", self.physics.world_width))
            self.gravity_input.setValue(self.physics.gravity)
            self.floor_input.setValue(self.physics.floor_y)
        self._rebuild_world_from_scene()

    def create_scene_node(self, shape: str) -> SceneNode:
        """Create a selectable scene primitive from the UI."""
        self.history.record(self.get_state())
        self._primitive_counter += 1
        node = SceneNode(
            f"{shape}_{self._primitive_counter}",
            display_name=f"{shape.title()} {self._primitive_counter}",
            x=80.0 + self._primitive_counter * 12.0,
            y=80.0 + self._primitive_counter * 12.0,
            width=80.0 if shape != "line" else 100.0,
            height=60.0 if shape != "line" else 0.0,
            color="#6a9955",
            shape=shape,
            collider_width=80.0 if shape != "line" else 100.0,
            collider_height=60.0 if shape != "line" else 1.0,
            points=[(80.0, 80.0), (140.0, 80.0), (110.0, 130.0)] if shape == "polygon" else [],
        )
        self.viewport.add_node(node)
        self.viewport.select_node(node.node_id)
        self._register_physics_node(node)
        self.engine_changed.emit()
        return node

    def update_selected_node(self, **updates: Any) -> bool:
        """Apply inspector-style updates to the selected node."""
        node = self.viewport.selected_node()
        if node is None:
            return False
        self.history.record(self.get_state())
        for key, value in updates.items():
            if key == "node_id":
                self._rename_selected_node(str(value))
                node = self.viewport.selected_node()
                if node is None:
                    return False
            elif hasattr(node, key):
                setattr(node, key, value)
        node.__post_init__()
        self._rebuild_world_from_scene()
        self.refresh_inspector()
        self.viewport.update()
        self.engine_changed.emit()
        return True

    def delete_selected_node(self) -> bool:
        """Delete the selected node with undo support."""
        if self.viewport.selected_node() is None:
            return False
        self.history.record(self.get_state())
        deleted = self.viewport.delete_selected()
        self._rebuild_world_from_scene()
        self.refresh_inspector()
        self.engine_changed.emit()
        return deleted

    def duplicate_selected_node(self) -> SceneNode | None:
        """Duplicate the selected node with undo support."""
        if self.viewport.selected_node() is None:
            return None
        self.history.record(self.get_state())
        duplicate = self.viewport.duplicate_selected()
        self._rebuild_world_from_scene()
        self.refresh_inspector()
        self.engine_changed.emit()
        return duplicate

    def undo(self) -> bool:
        """Undo the last Engine edit."""
        previous = self.history.undo(self.get_state())
        if previous is None:
            return False
        self.load_state(previous)
        self.engine_changed.emit()
        return True

    def redo(self) -> bool:
        """Redo the last undone Engine edit."""
        next_state = self.history.redo(self.get_state())
        if next_state is None:
            return False
        self.load_state(next_state)
        self.engine_changed.emit()
        return True

    def export_scene(self, path: str) -> str:
        """Export the current Engine scene to a standalone file."""
        return export_scene_file(path, self.get_state())

    def import_scene(self, path: str) -> None:
        """Import a standalone Engine scene file."""
        self.history.record(self.get_state())
        self.load_state(import_scene_file(path))
        self.engine_changed.emit()

    def play_physics(self) -> None:
        """Start live physics playback from the current edit state."""
        if not self._simulation_running:
            self._edit_baseline = self.viewport.get_state()
            self._rebuild_world_from_scene()
        self._simulation_running = True
        self._physics_timer.start()

    def pause_physics(self) -> None:
        """Pause live physics playback."""
        self._simulation_running = False
        self._physics_timer.stop()

    def reset_physics(self) -> None:
        """Stop simulation and restore the edit baseline if one exists."""
        self.pause_physics()
        if self._edit_baseline is not None:
            self.viewport.load_state(self._edit_baseline)
        self._rebuild_world_from_scene()

    def step_physics(self, dt: float = 1.0 / 60.0) -> None:
        """Advance physics one step and copy transforms back to scene nodes."""
        self._apply_physics_controls()
        self.physics.process(self.world, dt)
        for node_id, entity_id in self._node_entities.items():
            node = self.viewport.scene_graph.find_node(node_id)
            if node is None:
                continue
            transform = self.world.get_component(entity_id, Transform)
            node.x = transform.x
            node.y = transform.y
        self.viewport.update()
        self.engine_changed.emit()

    def _seed_default_scene(self) -> None:
        if self.viewport.scene_graph.nodes:
            return
        self.viewport.add_node(SceneNode("camera_frame", display_name="Camera Frame", x=40, y=40, width=220, height=140, color="#007acc", is_static=True))
        self.viewport.add_node(SceneNode("player_spawn", display_name="Player Spawn", x=125, y=85, width=48, height=48, color="#ff007f", shape="circle", collider_width=48, collider_height=48))
        self._rebuild_world_from_scene()
        self.refresh_inspector()
        self.engine_changed.emit()

    def dock_panel_factories(self):
        """Return live Engine dock panel factories keyed by panel name."""
        from app.ui.engine.panels import EnginePropertiesPanel, EngineSceneTreePanel
        from app.ui.themes.dock_panels import create_console_panel

        return {
            "Scene Tree": lambda: EngineSceneTreePanel(self),
            "Properties": lambda: EnginePropertiesPanel(self),
            "Console": create_console_panel,
        }

    def _apply_grid_controls(self) -> None:
        self.viewport.set_grid(self.grid_toggle.isChecked(), self.grid_size.value(), self.snap_toggle.isChecked())

    def _apply_physics_controls(self) -> None:
        self.physics.gravity = self.gravity_input.value()
        self.physics.floor_y = self.floor_input.value()

    def _rebuild_world_from_scene(self) -> None:
        self.world = World()
        self._node_entities = {}
        for node in self.viewport.scene_graph.nodes:
            self._register_physics_node(node)

    def _register_physics_node(self, node: SceneNode) -> None:
        entity = self.world.create_entity()
        self.world.add_component(entity, Transform(x=node.x, y=node.y))
        self.world.add_component(
            entity,
            RigidBody(
                velocity_x=node.velocity_x,
                velocity_y=node.velocity_y,
                mass=node.mass,
                is_static=node.is_static,
                restitution=node.restitution,
                friction=node.friction,
                damping=node.damping,
                gravity_scale=node.gravity_scale,
                collision_layer=node.collision_layer,
            ),
        )
        self.world.add_component(
            entity,
            Collider(
                width=max(node.collider_width if node.collider_width is not None else node.width, 1.0),
                height=max(node.collider_height if node.collider_height is not None else node.height, 1.0),
                is_trigger=node.is_trigger,
            ),
        )
        self._node_entities[node.node_id] = entity

    def _build_inspector(self) -> QWidget:
        panel = QWidget()
        form = QFormLayout(panel)
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
        apply_button = QPushButton("Apply")
        apply_button.clicked.connect(self.apply_inspector)
        form.addRow(apply_button)
        return panel

    def refresh_inspector(self, _node_id: str = "") -> None:
        node = self.viewport.selected_node()
        self._loading_inspector = True
        enabled = node is not None
        for widget in self.inspector.findChildren(QWidget):
            widget.setEnabled(enabled)
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
        self._loading_inspector = False

    def apply_inspector(self) -> None:
        if self._loading_inspector:
            return
        self.update_selected_node(
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

    def _rename_selected_node(self, new_node_id: str) -> None:
        if not new_node_id:
            raise SecurityError("Node id must not be empty.")
        node = self.viewport.selected_node()
        if node is None or node.node_id == new_node_id:
            return
        if self.viewport.scene_graph.find_node(new_node_id) is not None:
            raise SecurityError("Node id already exists.")
        old_node_id = node.node_id
        node.node_id = new_node_id
        self.viewport.selected_node_id = new_node_id
        if old_node_id in self._node_entities:
            self._node_entities[new_node_id] = self._node_entities.pop(old_node_id)

    def _spin(self, minimum: float, maximum: float, decimals: int = 3) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setDecimals(decimals)
        return spin

    def _install_shortcuts(self) -> None:
        shortcuts = (
            ("Delete", self.delete_selected_node),
            ("Ctrl+D", self.duplicate_selected_node),
            ("Ctrl+Z", self.undo),
            ("Ctrl+Y", self.redo),
            ("Space", self._toggle_physics),
            ("F", self.viewport.zoom_to_fit),
            ("G", lambda: self.grid_toggle.setChecked(not self.grid_toggle.isChecked())),
            ("S", lambda: self.snap_toggle.setChecked(not self.snap_toggle.isChecked())),
        )
        for sequence, callback in shortcuts:
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.activated.connect(callback)

    def _toggle_physics(self) -> None:
        if self._simulation_running:
            self.pause_physics()
        else:
            self.play_physics()
