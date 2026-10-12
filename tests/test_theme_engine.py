from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.security import SecurityError
from app.ui.themes import ThemeEngine


def test_theme_discovery_includes_default_builtins_and_custom(tmp_path: Path) -> None:
    themes_dir = tmp_path / "themes"
    themes_dir.mkdir()
    (themes_dir / "matrix.qss").write_text("QWidget { color: #00ff66; }\n", encoding="utf-8")
    (themes_dir / "notes.txt").write_text("ignore", encoding="utf-8")

    engine = ThemeEngine(themes_dir=str(themes_dir), config_dir=str(tmp_path / "config"))

    assert engine.list_themes() == ["dark", "default", "light", "matrix"]


def test_load_default_and_builtin_theme() -> None:
    engine = ThemeEngine()

    assert "QWidget" in engine.load_theme("default")
    assert "#1e1e1e" in engine.load_theme("dark")


def test_apply_theme_persists_preference_without_qapplication(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    engine = ThemeEngine(config_dir=str(config_dir))

    engine.apply_theme("light")

    assert engine.get_current_theme() == "light"
    restored = ThemeEngine(config_dir=str(config_dir))
    assert restored.load_saved_preference() == "light"


def test_missing_theme_raises_security_error(tmp_path: Path) -> None:
    engine = ThemeEngine(config_dir=str(tmp_path / "config"))

    with pytest.raises(SecurityError, match="Theme not found"):
        engine.load_theme("missing")


def test_theme_name_rejects_traversal_and_extensions(tmp_path: Path) -> None:
    engine = ThemeEngine(config_dir=str(tmp_path / "config"))

    with pytest.raises(SecurityError):
        engine.load_theme("../dark")

    with pytest.raises(SecurityError):
        engine.load_theme("dark.qss")


def test_invalid_saved_preference_falls_back_to_default(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    os_path = os.path.join(str(config_dir), "theme.json")
    Path(os_path).write_text('{"current_theme": "missing"}\n', encoding="utf-8")

    engine = ThemeEngine(config_dir=str(config_dir))

    assert engine.load_saved_preference() == "default"


def test_import_and_export_custom_theme(tmp_path: Path) -> None:
    source = tmp_path / "source.qss"
    source.write_text("QWidget { color: #123456; }\n", encoding="utf-8")
    themes_dir = tmp_path / "themes"
    export_path = tmp_path / "exported.qss"
    engine = ThemeEngine(themes_dir=str(themes_dir), config_dir=str(tmp_path / "config"))

    imported = engine.import_theme(str(source), "custom")
    exported = engine.export_theme("custom", str(export_path))

    assert Path(imported).read_text(encoding="utf-8") == source.read_text(encoding="utf-8")
    assert Path(exported).read_text(encoding="utf-8") == source.read_text(encoding="utf-8")
    assert "custom" in engine.list_themes()


def test_import_theme_rejects_bad_suffix_and_name(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("not qss", encoding="utf-8")
    engine = ThemeEngine(themes_dir=str(tmp_path / "themes"), config_dir=str(tmp_path / "config"))

    with pytest.raises(SecurityError):
        engine.import_theme(str(source), "custom")

    source_qss = tmp_path / "source.qss"
    source_qss.write_text("QWidget { color: #123456; }\n", encoding="utf-8")
    with pytest.raises(SecurityError):
        engine.import_theme(str(source_qss), "../custom")
