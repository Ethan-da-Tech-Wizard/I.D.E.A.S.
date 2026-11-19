# ==============================================================================
# Program: tests/test_dock_panels.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: tests app/ui/themes/dock_panels.py: DockablePanel (a titled dock wrapper) and PanelManager
# (registry of panel factories, creation/destruction, layout save/restore, per-mode layout files).
# Qt runs headless through the offscreen platform plugin.
# Run: python3 -m pytest tests/test_dock_panels.py -q    (needs PySide6)
# ==============================================================================

from __future__ import annotations

import os

# Must be set BEFORE the Qt imports below so no real display is required on CI machines.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel, QMainWindow

from app.security import SecurityError
from app.ui.themes import DockablePanel, PanelManager


# Function-scoped on purpose: the fixture returns the shared QApplication if one exists.
@pytest.fixture

def app() -> QApplication:
    """Return the process-wide QApplication (Qt allows only one), creating it if needed."""
    existing = QApplication.instance()
    if existing is not None:
        return existing
    return QApplication([])


# The panel must adopt the given title and content widget and install a custom title bar.
def test_dockable_panel_wraps_content(app: QApplication) -> None:
    content = QLabel("Scene")
    panel = DockablePanel("Scene Tree", content)

    assert panel.windowTitle() == "Scene Tree"
    assert panel.widget() is content
    assert panel.titleBarWidget() is not None


# Registration stores a factory (a callable returning a widget) by name; create_panel calls it, docks the result and
# reports it as active. A QMainWindow is the dock host.
def test_panel_manager_registers_and_creates_panel(app: QApplication) -> None:
    window = QMainWindow()
    manager = PanelManager(window)
    manager.register_panel("Console", lambda: QLabel("ready"))

    panel = manager.create_panel("Console", Qt.DockWidgetArea.BottomDockWidgetArea)

    assert panel.panel_name == "Console"
    assert manager.registered_panels() == ["Console"]
    assert manager.active_panels() == ["Console"]


# Registry integrity: registering a name twice or creating an unregistered one is refused with
# SecurityError so panels cannot be silently replaced.
def test_panel_manager_rejects_duplicate_and_unknown(app: QApplication) -> None:
    window = QMainWindow()
    manager = PanelManager(window)
    manager.register_panel("Console", lambda: QLabel("ready"))

    with pytest.raises(SecurityError):
        manager.register_panel("Console", lambda: QLabel("again"))

    with pytest.raises(SecurityError):
        manager.create_panel("Missing")


# The built-in demo panels register under their names and can all be created; lists are returned sorted.
def test_example_panels_are_registered_and_created(app: QApplication) -> None:
    window = QMainWindow()
    manager = PanelManager(window)
    manager.register_example_panels()

    for name in ("Scene Tree", "Properties", "Console"):
        manager.create_panel(name)

    assert manager.registered_panels() == ["Console", "Properties", "Scene Tree"]
    assert manager.active_panels() == ["Console", "Properties", "Scene Tree"]


# Layout state (dock positions) is saved to a non-empty value that restore_layout accepts, so a
# user's arrangement survives a restart.
def test_layout_serialization_round_trip(app: QApplication) -> None:
    window = QMainWindow()
    manager = PanelManager(window)
    manager.register_panel("Console", lambda: QLabel("ready"))
    manager.create_panel("Console")

    saved = manager.save_layout()

    assert saved
    assert manager.restore_layout(saved) is True


# destroy_panel returns True the first time and False when the panel is already gone (idempotent).
def test_destroy_panel_reports_state(app: QApplication) -> None:
    window = QMainWindow()
    manager = PanelManager(window)
    manager.register_panel("Console", lambda: QLabel("ready"))
    manager.create_panel("Console")

    assert manager.destroy_panel("Console") is True
    assert manager.destroy_panel("Console") is False


# Each application mode (here "engine") saves its layout to <name>.json in the given folder and can restore it.
def test_mode_layout_persistence(app: QApplication, tmp_path) -> None:
    window = QMainWindow()
    manager = PanelManager(window)
    manager.register_panel("Console", lambda: QLabel("ready"))
    manager.create_panel("Console")

    path = manager.save_mode_layout("engine", str(tmp_path))

    assert path.endswith("engine.json")
    assert manager.restore_mode_layout("engine", str(tmp_path)) is True


# The mode name becomes a file name, so "../engine" must be rejected to prevent writing outside the folder.
def test_mode_layout_rejects_unsafe_name(app: QApplication, tmp_path) -> None:
    window = QMainWindow()
    manager = PanelManager(window)

    with pytest.raises(SecurityError):
        manager.save_mode_layout("../engine", str(tmp_path))
