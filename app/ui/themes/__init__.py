# ==============================================================================
# Program: app/ui/themes/__init__.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: package entry point for theming and dockable panels. Re-exports ThemeEngine (stylesheet
# loading/persistence, theme_engine.py) and DockablePanel/PanelManager (dock_panels.py) so callers
# use `from app.ui.themes import ThemeEngine`. Imports PySide6 through dock_panels.
# ==============================================================================

"""Theming engine and dockable panel system."""

from app.ui.themes.dock_panels import DockablePanel, PanelManager
from app.ui.themes.theme_engine import ThemeEngine

# Public API of the package; also marks the re-imports as intentional for linters.
__all__ = ["DockablePanel", "PanelManager", "ThemeEngine"]
