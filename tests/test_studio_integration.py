# ==============================================================================
# Program: tests/test_studio_integration.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: smoke tests for the studio feature set: the video timeline, photo editor, MIDI service,
# plugin bridge and asset manager modules must import, and MainWindow must still expose the
# builder/switch hooks that attach them to the UI. They catch broken wiring after refactors.
# Run: python3 -m pytest tests/test_studio_integration.py -q     (needs PySide6)
# ==============================================================================

from __future__ import annotations


# Importing exposes syntax errors, missing dependencies and circular imports; noqa silences the unused-import lint.
def test_studio_modules_import_cleanly() -> None:
    import app.ui.studio.video_timeline  # noqa: F401
    import app.ui.studio.photo_editor  # noqa: F401
    import app.services.audio.midi_service  # noqa: F401
    import app.services.audio.plugin_bridge  # noqa: F401
    import app.ui.assets.asset_manager  # noqa: F401


# Interface guard: each studio mode has a build_*_mode method, assets are routed via
# _route_asset_activation and modes are switched with switch_mode. Renaming any of them without
# updating callers would break the mode buttons or asset double-click.
def test_application_module_wires_up_studio_builders() -> None:
    from app import application

    assert hasattr(application.MainWindow, "build_video_editor_mode")
    assert hasattr(application.MainWindow, "build_photo_editor_mode")
    assert hasattr(application.MainWindow, "build_asset_manager_mode")
    assert hasattr(application.MainWindow, "_route_asset_activation")
    assert hasattr(application.MainWindow, "switch_mode")
