from __future__ import annotations

import base64
import json
import os
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QByteArray, QStandardPaths, Qt
from PySide6.QtWidgets import (
    QLabel,
    QDockWidget,
    QMainWindow,
    QPushButton,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.security import SecurityError


PanelFactory = Callable[[], QWidget]


class DockablePanel(QDockWidget):
    """Dock widget wrapper with a compact custom title bar."""

    def __init__(self, panel_name: str, content: QWidget, parent: QWidget | None = None) -> None:
        super().__init__(panel_name, parent)
        self.panel_name = panel_name
        self.setObjectName(f"DockablePanel_{self._object_suffix(panel_name)}")
        self.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)
        self.setWidget(content)
        self.setTitleBarWidget(self._build_title_bar(panel_name))

    def _build_title_bar(self, panel_name: str) -> QWidget:
        title_bar = QWidget(self)
        title_bar.setObjectName("DockPanelTitleBar")
        layout = QVBoxLayout(title_bar)
        layout.setContentsMargins(8, 4, 4, 4)
        layout.setSpacing(4)

        label = QLabel(panel_name)
        label.setObjectName("panelTitle")
        close_button = QPushButton("x")
        close_button.setObjectName("DockPanelCloseButton")
        close_button.setFixedSize(22, 22)
        close_button.clicked.connect(self.close)

        row = QWidget(title_bar)
        row_layout = QVBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.addWidget(label)
        row_layout.addWidget(close_button, alignment=Qt.AlignmentFlag.AlignRight)
        layout.addWidget(row)
        return title_bar

    def _object_suffix(self, panel_name: str) -> str:
        return "".join(ch if ch.isalnum() else "_" for ch in panel_name).strip("_") or "Panel"


class PanelManager:
    """Registers, creates, destroys, and serializes dockable panels."""

    def __init__(self, main_window: QMainWindow) -> None:
        self._main_window = main_window
        self._factories: dict[str, PanelFactory] = {}
        self._panels: dict[str, DockablePanel] = {}

    def register_panel(self, name: str, factory: PanelFactory) -> None:
        """Register a panel factory by display name."""
        if not name:
            raise SecurityError("Panel name must not be empty.")
        if name in self._factories:
            raise SecurityError(f"Panel is already registered: {name}")
        self._factories[name] = factory

    def create_panel(
        self,
        name: str,
        area: Qt.DockWidgetArea = Qt.DockWidgetArea.RightDockWidgetArea,
    ) -> DockablePanel:
        """Create and attach a panel to the managed main window."""
        if name not in self._factories:
            raise SecurityError(f"Unknown panel: {name}")
        if name in self._panels:
            return self._panels[name]

        panel = DockablePanel(name, self._factories[name](), self._main_window)
        self._main_window.addDockWidget(area, panel)
        self._panels[name] = panel
        panel.destroyed.connect(lambda _obj=None, panel_name=name: self._panels.pop(panel_name, None))
        return panel

    def destroy_panel(self, name: str) -> bool:
        """Remove and close a named panel if it exists."""
        panel = self._panels.pop(name, None)
        if panel is None:
            return False
        self._main_window.removeDockWidget(panel)
        panel.close()
        panel.deleteLater()
        return True

    def active_panels(self) -> list[str]:
        """Return sorted names for currently attached panels."""
        return sorted(self._panels)

    def registered_panels(self) -> list[str]:
        """Return sorted names for registered panel factories."""
        return sorted(self._factories)

    def save_layout(self) -> bytes:
        """Serialize the current QMainWindow dock layout."""
        return bytes(self._main_window.saveState())

    def restore_layout(self, data: bytes) -> bool:
        """Restore a previously serialized QMainWindow dock layout."""
        if not isinstance(data, bytes):
            raise SecurityError("Panel layout data must be bytes.")
        return self._main_window.restoreState(QByteArray(data))

    def save_mode_layout(self, mode_name: str, config_dir: str | None = None) -> str:
        """Persist this window's dock layout for one app mode."""
        self._validate_mode_name(mode_name)
        directory = self._layout_config_dir(config_dir)
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, f"{mode_name}.json")
        payload = {"mode": mode_name, "layout": base64.b64encode(self.save_layout()).decode("ascii")}
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        return path

    def restore_mode_layout(self, mode_name: str, config_dir: str | None = None) -> bool:
        """Restore a previously persisted dock layout for one app mode."""
        self._validate_mode_name(mode_name)
        path = os.path.join(self._layout_config_dir(config_dir), f"{mode_name}.json")
        if not os.path.exists(path):
            return False
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        encoded = payload.get("layout") if isinstance(payload, dict) else None
        if not isinstance(encoded, str):
            raise SecurityError("Panel layout payload is invalid.")
        return self.restore_layout(base64.b64decode(encoded.encode("ascii")))

    def register_example_panels(self) -> None:
        """Register the default Scene Tree, Properties, and Console panels."""
        self.register_panel("Scene Tree", create_scene_tree_panel)
        self.register_panel("Properties", create_properties_panel)
        self.register_panel("Console", create_console_panel)

    def _validate_mode_name(self, mode_name: str) -> None:
        if not mode_name or Path(mode_name).name != mode_name or "." in mode_name:
            raise SecurityError("Mode layout name must be a simple name.")

    def _layout_config_dir(self, config_dir: str | None) -> str:
        if config_dir is not None:
            return os.path.abspath(config_dir)
        app_data = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
        if not app_data:
            app_data = os.path.join(Path.home(), ".local", "share", "id_app")
        return os.path.abspath(os.path.join(app_data, "layouts"))


def create_scene_tree_panel() -> QWidget:
    """Create a placeholder scene hierarchy panel."""
    tree = QTreeWidget()
    tree.setHeaderLabel("Scene")
    root = QTreeWidgetItem(["Root"])
    root.addChild(QTreeWidgetItem(["Camera"]))
    root.addChild(QTreeWidgetItem(["Light"]))
    root.addChild(QTreeWidgetItem(["Player"]))
    tree.addTopLevelItem(root)
    tree.expandAll()
    return tree


def create_properties_panel() -> QWidget:
    """Create a placeholder property editor panel."""
    container = QWidget()
    layout = QVBoxLayout(container)
    layout.setContentsMargins(10, 10, 10, 10)
    layout.setSpacing(8)
    layout.addWidget(QLabel("Transform"))
    layout.addWidget(QLabel("Position: 0, 0"))
    layout.addWidget(QLabel("Scale: 1, 1"))
    layout.addStretch()
    return container


def create_console_panel() -> QWidget:
    """Create a placeholder engine console panel."""
    console = QTextEdit()
    console.setReadOnly(True)
    console.setPlainText("> Engine console ready")
    return console
