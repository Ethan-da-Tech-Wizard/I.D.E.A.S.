from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from PySide6.QtCore import QStandardPaths
from PySide6.QtWidgets import QApplication

from app.security import SecurityError
from app.storage.workspace import is_safe_subpath


MAX_THEME_BYTES = 256 * 1024
BUILTIN_THEME_DIR = Path(__file__).resolve().parent / "builtins"
DEFAULT_STYLE_PATH = Path(__file__).resolve().parents[1] / "style.qss"


class ThemeEngine:
    """Loads local QSS themes and persists the active theme preference."""

    def __init__(
        self,
        themes_dir: str | None = None,
        config_dir: str | None = None,
        app: QApplication | None = None,
    ) -> None:
        self._app = app
        self._builtin_dir = str(BUILTIN_THEME_DIR)
        self._default_style_path = str(DEFAULT_STYLE_PATH)
        self._themes_dir = os.path.abspath(themes_dir) if themes_dir else None
        self._config_dir = os.path.abspath(config_dir) if config_dir else self._default_config_dir()
        self._current_theme = "default"

    def list_themes(self) -> list[str]:
        """Return all available local theme names without file extensions."""
        themes = {"default"}
        themes.update(self._scan_theme_dir(self._builtin_dir))
        if self._themes_dir is not None:
            themes.update(self._scan_theme_dir(self._themes_dir))
        return sorted(themes)

    def load_theme(self, theme_name: str) -> str:
        """Load and return the QSS content for a named theme."""
        theme_path = self._resolve_theme_path(theme_name)
        self._validate_theme_file(theme_path)
        with open(theme_path, "r", encoding="utf-8") as handle:
            return handle.read()

    def apply_theme(self, theme_name: str) -> None:
        """Apply a named theme to the configured QApplication and persist it."""
        stylesheet = self.load_theme(theme_name)
        if self._app is not None:
            self._app.setStyleSheet(stylesheet)
        self._current_theme = theme_name
        self._save_preference(theme_name)

    def import_theme(self, source_path: str, theme_name: str) -> str:
        """Import a local `.qss` file into the configured custom theme directory."""
        if self._themes_dir is None:
            raise SecurityError("A custom themes directory is required for imports.")
        self._validate_theme_name(theme_name)
        source = Path(source_path).expanduser().resolve(strict=True)
        if source.suffix.lower() != ".qss":
            raise SecurityError("Imported theme must be a .qss file.")
        self._validate_theme_file(str(source))
        os.makedirs(self._themes_dir, exist_ok=True)
        destination = os.path.abspath(os.path.join(self._themes_dir, f"{theme_name}.qss"))
        if not is_safe_subpath(self._themes_dir, destination):
            raise SecurityError("Theme import path escapes themes directory.")
        shutil.copyfile(str(source), destination)
        return destination

    def export_theme(self, theme_name: str, destination_path: str) -> str:
        """Export a named theme to an explicit local `.qss` destination."""
        source = self._resolve_theme_path(theme_name)
        destination = Path(destination_path).expanduser()
        if destination.suffix.lower() != ".qss":
            destination = destination.with_suffix(".qss")
        parent = destination.parent.resolve(strict=True)
        if not parent.is_dir():
            raise SecurityError("Theme export parent must be a directory.")
        resolved_destination = parent / destination.name
        if resolved_destination.exists() and not resolved_destination.is_file():
            raise SecurityError("Theme export destination must be a regular file.")
        shutil.copyfile(source, str(resolved_destination))
        return str(resolved_destination)

    def get_current_theme(self) -> str:
        """Return the active theme name."""
        return self._current_theme

    def load_saved_preference(self) -> str:
        """Load the saved theme preference, falling back to default."""
        config_path = self._config_path()
        if not os.path.exists(config_path):
            self._current_theme = "default"
            return self._current_theme

        self._validate_config_path(config_path)
        try:
            with open(config_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except json.JSONDecodeError:
            self._current_theme = "default"
            return self._current_theme

        theme_name = data.get("current_theme") if isinstance(data, dict) else None
        if isinstance(theme_name, str) and theme_name in self.list_themes():
            self._current_theme = theme_name
        else:
            self._current_theme = "default"
        return self._current_theme

    def _scan_theme_dir(self, directory: str) -> set[str]:
        if not os.path.isdir(directory):
            return set()

        names: set[str] = set()
        base = os.path.abspath(directory)
        for entry in os.scandir(base):
            if not entry.is_file() or not entry.name.endswith(".qss"):
                continue
            candidate = os.path.abspath(entry.path)
            if is_safe_subpath(base, candidate):
                names.add(Path(entry.name).stem)
        return names

    def _resolve_theme_path(self, theme_name: str) -> str:
        self._validate_theme_name(theme_name)
        if theme_name == "default":
            return self._default_style_path

        candidates = [os.path.join(self._builtin_dir, f"{theme_name}.qss")]
        if self._themes_dir is not None:
            candidates.append(os.path.join(self._themes_dir, f"{theme_name}.qss"))

        for candidate in candidates:
            base = os.path.abspath(os.path.dirname(candidate))
            full_path = os.path.abspath(candidate)
            if is_safe_subpath(base, full_path) and os.path.isfile(full_path):
                return full_path
        raise SecurityError(f"Theme not found: {theme_name}")

    def _validate_theme_name(self, theme_name: str) -> None:
        if not isinstance(theme_name, str) or not theme_name:
            raise SecurityError("Theme name must be text.")
        if theme_name != Path(theme_name).name or Path(theme_name).suffix:
            raise SecurityError("Theme name cannot contain path separators or extensions.")

    def _validate_theme_file(self, theme_path: str) -> None:
        full_path = os.path.abspath(theme_path)
        valid_roots = [self._builtin_dir, os.path.dirname(self._default_style_path), os.path.dirname(full_path)]
        if self._themes_dir is not None:
            valid_roots.append(self._themes_dir)

        if not any(is_safe_subpath(root, full_path) for root in valid_roots):
            raise SecurityError("Theme path escapes allowed theme directories.")
        if not os.path.isfile(full_path):
            raise SecurityError("Theme file must be a regular file.")
        if os.path.getsize(full_path) > MAX_THEME_BYTES:
            raise SecurityError("Theme file is too large.")

    def _save_preference(self, theme_name: str) -> None:
        os.makedirs(self._config_dir, exist_ok=True)
        config_path = self._config_path()
        self._validate_config_path(config_path)
        payload: dict[str, Any] = {"current_theme": theme_name}
        with open(config_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")

    def _config_path(self) -> str:
        return os.path.join(self._config_dir, "theme.json")

    def _validate_config_path(self, config_path: str) -> None:
        if not is_safe_subpath(self._config_dir, os.path.abspath(config_path)):
            raise SecurityError("Theme preference path escapes config directory.")

    def _default_config_dir(self) -> str:
        app_data = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
        if not app_data:
            app_data = os.path.join(Path.home(), ".local", "share", "id_app")
        return os.path.abspath(os.path.join(app_data, "themes"))
