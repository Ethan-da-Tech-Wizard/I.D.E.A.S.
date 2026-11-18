# ==============================================================================
# Program: __init__.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: Package initialization marker establishing the namespace and public exports for the themes module.
# ==============================================================================

"""Theming engine and dockable panel system."""

from app.ui.themes.dock_panels import DockablePanel, PanelManager
from app.ui.themes.theme_engine import ThemeEngine

__all__ = ["DockablePanel", "PanelManager", "ThemeEngine"]
