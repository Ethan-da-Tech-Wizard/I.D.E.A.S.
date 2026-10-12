from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel, QMainWindow

from app.security import SecurityError
from app.ui.themes import DockablePanel, PanelManager


@pytest.fixture
def app() -> QApplication:
    existing = QApplication.instance()
    if existing is not None:
        return existing
    return QApplication([])


def test_dockable_panel_wraps_content(app: QApplication) -> None:
    content = QLabel("Scene")
    panel = DockablePanel("Scene Tree", content)

    assert panel.windowTitle() == "Scene Tree"
    assert panel.widget() is content
    assert panel.titleBarWidget() is not None


def test_panel_manager_registers_and_creates_panel(app: QApplication) -> None:
    window = QMainWindow()
    manager = PanelManager(window)
    manager.register_panel("Console", lambda: QLabel("ready"))

    panel = manager.create_panel("Console", Qt.DockWidgetArea.BottomDockWidgetArea)

    assert panel.panel_name == "Console"
    assert manager.registered_panels() == ["Console"]
    assert manager.active_panels() == ["Console"]


def test_panel_manager_rejects_duplicate_and_unknown(app: QApplication) -> None:
    window = QMainWindow()
    manager = PanelManager(window)
    manager.register_panel("Console", lambda: QLabel("ready"))

    with pytest.raises(SecurityError):
        manager.register_panel("Console", lambda: QLabel("again"))

    with pytest.raises(SecurityError):
        manager.create_panel("Missing")


def test_example_panels_are_registered_and_created(app: QApplication) -> None:
    window = QMainWindow()
    manager = PanelManager(window)
    manager.register_example_panels()

    for name in ("Scene Tree", "Properties", "Console"):
        manager.create_panel(name)

    assert manager.registered_panels() == ["Console", "Properties", "Scene Tree"]
    assert manager.active_panels() == ["Console", "Properties", "Scene Tree"]


def test_layout_serialization_round_trip(app: QApplication) -> None:
    window = QMainWindow()
    manager = PanelManager(window)
    manager.register_panel("Console", lambda: QLabel("ready"))
    manager.create_panel("Console")

    saved = manager.save_layout()

    assert saved
    assert manager.restore_layout(saved) is True


def test_destroy_panel_reports_state(app: QApplication) -> None:
    window = QMainWindow()
    manager = PanelManager(window)
    manager.register_panel("Console", lambda: QLabel("ready"))
    manager.create_panel("Console")

    assert manager.destroy_panel("Console") is True
    assert manager.destroy_panel("Console") is False


def test_mode_layout_persistence(app: QApplication, tmp_path) -> None:
    window = QMainWindow()
    manager = PanelManager(window)
    manager.register_panel("Console", lambda: QLabel("ready"))
    manager.create_panel("Console")

    path = manager.save_mode_layout("engine", str(tmp_path))

    assert path.endswith("engine.json")
    assert manager.restore_mode_layout("engine", str(tmp_path)) is True


def test_mode_layout_rejects_unsafe_name(app: QApplication, tmp_path) -> None:
    window = QMainWindow()
    manager = PanelManager(window)

    with pytest.raises(SecurityError):
        manager.save_mode_layout("../engine", str(tmp_path))
