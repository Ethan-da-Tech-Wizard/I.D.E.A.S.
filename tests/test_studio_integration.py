from __future__ import annotations


def test_studio_modules_import_cleanly() -> None:
    import app.ui.studio.video_timeline  # noqa: F401
    import app.ui.studio.photo_editor  # noqa: F401
    import app.services.audio.midi_service  # noqa: F401
    import app.services.audio.plugin_bridge  # noqa: F401
    import app.ui.assets.asset_manager  # noqa: F401


def test_application_module_wires_up_studio_builders() -> None:
    from app import application

    assert hasattr(application.MainWindow, "build_video_editor_mode")
    assert hasattr(application.MainWindow, "build_photo_editor_mode")
    assert hasattr(application.MainWindow, "build_asset_manager_mode")
    assert hasattr(application.MainWindow, "_route_asset_activation")
    assert hasattr(application.MainWindow, "switch_mode")
