# ==============================================================================
# Program: asset_manager.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: Organizes project creative assets (textures, audio samples, 3D models, shaders, scripts) with search filtering, metadata tagging, and preview thumbnails.
# ==============================================================================
# How this module fits: it is the "Assets" mode of the main window (activity-bar button 6). app/application.py builds AssetManagerWidget lazily
# (build_asset_manager_mode) with the open workspace as its root, connects asset_activated to MainWindow._route_asset_activation (which opens the file in the photo editor, video
# timeline or DAW) and saves/restores the widget through get_state()/load_state() as part of a workspace. The photo editor, video timeline and DAW also accept DROPS carrying the
# custom MIME type defined here (ASSET_MIME_TYPE), which they route through the same function.
# Structure: functions and AssetManagerModel are pure Python (no Qt), so tests/test_asset_manager.py can run them without a display; AssetListModel and AssetManagerWidget are
# the Qt layer. File discovery and safety rules live in app/ui/assets/catalog.py.
# Inputs: a workspace folder; user actions (search, filter, click, double click, drag, context menu). Outputs: the asset_activated(relative_path) signal, drag payloads, and file renames/deletes on disk.
# Design decisions: the drag payload is just the UTF-8 relative path (decoded and validated by decode_asset_mime_payload, never trusted); rename and delete are confined to the root by a containment
# check; the catalog is rebuilt from disk after every change instead of being patched in place, which is slower but cannot drift from reality; non-image assets get a colour-coded text swatch because
# decoding media for previews is out of scope (see the threat model).
# Known limits and risks noticed (comment pass only):
#   - The folder tree is flat: every folder (even nested ones like "a/b") appears as a top-level row labelled with its relative path.
#   - AssetManagerModel._resolve checks the RESOLVED (symlink-free) candidate against the UN-resolved root text, so a root reached through a symlink (for example /tmp on macOS) makes rename and delete
#     fail with "escapes the workspace root" even for ordinary files.
#   - rename_asset does not require the new name to keep a supported extension, so renaming "a.png" to "a.bin" makes the asset disappear from the catalog.
#   - delete_asset is permanent (no trash) and only the widget, not the model, asks for confirmation.
#   - load_state trusts root_path from a project file without validation and can raise FileNotFoundError if the folder is gone; the folder is only listed, but delete/rename then act inside it.
#   - Image thumbnails are decoded by Qt for every image up to the 50 MiB catalog limit, on the GUI thread, which can stall the window on large pictures.

"""Unified asset browser: a pure-Python catalog/filter model plus a Qt tree+list UI.

AssetManagerModel wraps app.ui.assets.catalog.build_asset_catalog and adds
search/kind filtering and validated rename/delete operations, all without any
Qt dependency, so it can be unit-tested without a QApplication.
AssetManagerWidget renders that model as a QTreeView (folder structure) plus a
QListView (assets in the selected folder), with search, filter, drag-and-drop,
and a context menu on top.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QMimeData, QSize, QUrl, Qt, Signal
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QImage, QPainter, QPixmap, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListView,
    QMenu,
    QMessageBox,
    QSplitter,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from app.security import SecurityError
from app.storage.workspace import is_safe_subpath
from app.ui.assets.catalog import ALLOWED_ASSET_SUFFIXES, AssetCatalog, AssetEntry, AssetKind, build_asset_catalog

# Filter drop-down: "All" means no kind filter; the other labels are the AssetKind values capitalised ("Audio", "Image", ...), generated from the enum so new kinds appear automatically.
FILTER_ALL = "All"
_KIND_BY_FILTER_LABEL: dict[str, Optional[AssetKind]] = {FILTER_ALL: None}
for _kind in AssetKind:
    _KIND_BY_FILTER_LABEL[_kind.value.capitalize()] = _kind

# Thumbnail edge in pixels (48 x 48), small enough that long lists stay fast.
THUMBNAIL_SIZE = 48

# Swatch colour per kind for assets without a real preview; hues match the app's neon theme.
_KIND_PLACEHOLDER_COLORS: dict[AssetKind, str] = {
    AssetKind.AUDIO: "#3ddc97",
    AssetKind.IMAGE: "#00f0ff",
    AssetKind.MODEL: "#a26bfa",
    AssetKind.SCRIPT: "#ffd23f",
    AssetKind.VIDEO: "#ff5f1f",
}

# Custom MIME type for drag and drop. The "x-" prefix marks it as application-specific; no other program understands it, so a drag cannot be dropped into a foreign app by accident.
ASSET_MIME_TYPE = "application/x-ideas-asset"


def encode_asset_mime_payload(relative_path: str) -> bytes:
    """Encode the drag payload for an asset.

    Args:
        relative_path: asset path relative to the asset manager root.

    Returns:
        UTF-8 bytes of the path. Any widget that receives a drop can forward the decoded path straight into the same routing
        MainWindow._route_asset_activation() already uses for double-click (see AssetManagerWidget.asset_activated).

    Raises:
        SecurityError: the path is empty.
    """
    if not relative_path:
        raise SecurityError("Cannot drag an empty asset path.")
    return relative_path.encode("utf-8")


def decode_asset_mime_payload(data: bytes) -> str:
    """Decode a drop payload back into a relative path.

    Args:
        data: raw bytes (or a QByteArray) from QMimeData.

    Returns:
        The path text. The caller must still validate it (the receiving widgets pass it through the normal import checks).

    Raises:
        SecurityError: not valid UTF-8 or empty, rather than handing a target widget garbage.
    """
    try:
        relative_path = bytes(data).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SecurityError("Dropped asset payload is not valid UTF-8.") from exc
    if not relative_path:
        raise SecurityError("Dropped asset payload is empty.")
    return relative_path


def _folder_of(relative_path: str) -> str:
    """Return the folder part of a relative path ("" for files directly in the root)."""
    # Path("x.wav").parent is "." and the model uses "" for the root folder, hence the translation.
    parent = str(Path(relative_path).parent)
    return "" if parent == "." else parent


def kind_for_relative_path(relative_path: str) -> Optional[AssetKind]:
    """Map a file suffix to its AssetKind without needing a catalog.

    Args:
        relative_path: any path or file name.

    Returns:
        AssetKind, or None for an unsupported extension (case-insensitive). Used to route an activated asset to the right editor (Photo/Video/DAW).
    """
    return ALLOWED_ASSET_SUFFIXES.get(Path(relative_path).suffix.lower())


class AssetManagerModel:
    """Catalog plus filtering and safe file operations, with no Qt dependency."""

    def __init__(self, root_path: str) -> None:
        """Scan the folder immediately.

        Args:
            root_path: workspace folder; stored as an absolute path.

        Raises:
            SecurityError / FileNotFoundError: from build_asset_catalog if the folder is invalid.
        """
        self.root_path = os.path.abspath(root_path)
        self.catalog: AssetCatalog = build_asset_catalog(self.root_path)

    def refresh(self) -> None:
        """Rescan the disk and replace the catalog (the previous one is discarded)."""
        self.catalog = build_asset_catalog(self.root_path)

    def folders(self) -> list[str]:
        """Return the sorted list of folders that contain assets, always including "" (the root)."""
        folders = {""}
        for entry in self.catalog.entries:
            folders.add(_folder_of(entry.relative_path))
        return sorted(folders)

    def filter_entries(
        self,
        *,
        folder: Optional[str] = None,
        search_text: str = "",
        kind: Optional[AssetKind] = None,
    ) -> list[AssetEntry]:
        """Select assets by folder, name text and kind.

        Args:
            folder: relative folder to show; "" = files directly in the root; None = every folder.
            search_text: case-insensitive substring of the file NAME (not the path); blank matches all.
            kind: AssetKind to keep, or None for all kinds.

        Returns:
            Matching AssetEntry objects in catalog (path) order.
        """
        # Normalise once outside the loop.
        needle = search_text.strip().lower()
        results = []
        for entry in self.catalog.entries:
            if kind is not None and entry.kind != kind:
                continue
            if folder is not None and _folder_of(entry.relative_path) != folder:
                continue
            if needle and needle not in entry.name.lower():
                continue
            results.append(entry)
        return results

    def _resolve(self, relative_path: str) -> Path:
        """Resolve a relative path to a real path and make sure it stays inside the root.

        Raises:
            SecurityError: the resolved path is outside the root (see the symlink caveat in the file header).
        """
        candidate = (Path(self.root_path) / relative_path).resolve(strict=False)
        if not is_safe_subpath(self.root_path, str(candidate)):
            raise SecurityError("Asset path escapes the workspace root.")
        return candidate

    def rename_asset(self, relative_path: str, new_name: str) -> str:
        """Rename a file in place within its folder.

        Args:
            relative_path: current path of the asset.
            new_name: new file name only (no folder).

        Returns:
            The new relative path with forward slashes. The catalog is refreshed.

        Raises:
            SecurityError: empty name, a name containing a path separator or "."/"..", source missing, or the destination already exists.
        """
        # A name containing a separator could move the file into another folder; "." and ".." are also refused.
        if not new_name or any(sep in new_name for sep in ("/", "\\")) or new_name in {".", ".."}:
            raise SecurityError("Invalid asset name.")
        source = self._resolve(relative_path)
        if not source.is_file():
            raise SecurityError("Asset does not exist.")
        # Same folder, new name; the containment check runs again on the destination.
        destination = self._resolve(str(Path(relative_path).parent / new_name))
        # Never overwrite an existing file.
        if destination.exists():
            raise SecurityError("An asset with that name already exists.")
        source.rename(destination)
        new_relative_path = str((Path(relative_path).parent / new_name)).replace(os.sep, "/")
        self.refresh()
        return new_relative_path

    def delete_asset(self, relative_path: str) -> None:
        """Delete one asset file permanently and refresh the catalog.

        Raises:
            SecurityError: path outside the root or not an existing regular file.
        """
        target = self._resolve(relative_path)
        if not target.is_file():
            raise SecurityError("Asset does not exist.")
        target.unlink()
        self.refresh()

    def folder_disk_path(self, folder: str) -> Path:
        """Absolute path of a folder in the catalog ("" gives the root)."""
        return self._resolve(folder) if folder else Path(self.root_path)


class AssetListModel(QStandardItemModel):
    """List model whose drag payload is our own MIME type (ASSET_MIME_TYPE) carrying the asset's relative path, instead of Qt's default internal
    item-data-list format, so any widget can accept the drop without knowing anything about QStandardItemModel's encoding.
    """

    # Qt calls these camel-case names when a drag starts, hence the noqa for the lint rule on naming.
    def mimeTypes(self) -> list[str]:  # noqa: N802 - Qt override
        """Advertise the one MIME type this model can export."""
        return [ASSET_MIME_TYPE]

    def mimeData(self, indexes) -> QMimeData:  # noqa: N802 - Qt override
        """Build the drag payload for the selected row.

        Args:
            indexes: model indexes being dragged (the view is single-selection, so normally one).

        Returns:
            QMimeData with the first valid index's path stored under ASSET_MIME_TYPE; an empty QMimeData if none is valid.
        """
        mime = QMimeData()
        for index in indexes:
            if not index.isValid():
                continue
            # The path is stored in UserRole when the row is created (see _rebuild_list).
            relative_path = index.data(Qt.ItemDataRole.UserRole)
            if relative_path:
                mime.setData(ASSET_MIME_TYPE, encode_asset_mime_payload(relative_path))
                # Only one asset per drag: stop after the first.
                break
        return mime


class AssetManagerWidget(QWidget):
    """Qt widget showing folders on the left and the matching assets on the right.

    Signals:
        asset_activated(str): emitted with the relative path when the user double-clicks an asset.
    """

    # Signal carrying a str; connected by MainWindow to route the asset to an editor.
    asset_activated = Signal(str)

    def __init__(self, root_path: str, parent: QWidget | None = None) -> None:
        """Build the model and the UI and fill them.

        Args:
            root_path: folder to browse.
            parent: optional parent widget.
        """
        super().__init__(parent)
        # The object name is a hook for the application style sheet.
        self.setObjectName("AssetManagerWidget")
        self.model = AssetManagerModel(root_path)
        self._current_folder = ""
        self.setup_ui()
        self.refresh()

    def setup_ui(self) -> None:
        """Create the search bar, the folder tree and the asset list."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

        # Top bar: free-text search plus a kind drop-down; any change rebuilds the list (_on_filters_changed).
        top_bar = QHBoxLayout()
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search assets…")
        self.search_box.textChanged.connect(self._on_filters_changed)

        self.kind_filter = QComboBox()
        self.kind_filter.addItems(list(_KIND_BY_FILTER_LABEL.keys()))
        self.kind_filter.currentTextChanged.connect(self._on_filters_changed)

        top_bar.addWidget(QLabel("Search:"))
        top_bar.addWidget(self.search_box, 1)
        top_bar.addWidget(QLabel("Type:"))
        top_bar.addWidget(self.kind_filter)
        layout.addLayout(top_bar)

        # Left: folder tree; right: asset list. A single-selection list with dragging enabled starts our custom drag.
        splitter = QSplitter(Qt.Orientation.Horizontal)

        self.tree_view = QTreeView()
        self.tree_model = QStandardItemModel()
        self.tree_model.setHorizontalHeaderLabels(["Folders"])
        self.tree_view.setModel(self.tree_model)
        self.tree_view.clicked.connect(self._on_folder_selected)
        splitter.addWidget(self.tree_view)

        self.list_view = QListView()
        self.list_model = AssetListModel()
        self.list_view.setModel(self.list_model)
        self.list_view.setDragEnabled(True)
        self.list_view.setSelectionMode(self.list_view.SelectionMode.SingleSelection)
        # Custom context-menu policy so right-clicks arrive in _on_context_menu (rename, delete, show in explorer) instead of the default (none).
        self.list_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list_view.customContextMenuRequested.connect(self._on_context_menu)
        self.list_view.doubleClicked.connect(self._on_asset_double_clicked)
        splitter.addWidget(self.list_view)

        # Initial split of 200 px for the folder tree and 500 px for the list; the user can drag the splitter.
        splitter.setSizes([200, 500])
        layout.addWidget(splitter, 1)

    def refresh(self) -> None:
        """Rescan the disk and redraw both the tree and the list."""
        self.model.refresh()
        self._rebuild_tree()
        self._rebuild_list()

    def _rebuild_tree(self) -> None:
        """Refill the folder tree from the model. Row data (UserRole) holds the relative folder path, "" for the root row."""
        self.tree_model.removeRows(0, self.tree_model.rowCount())
        root_item = QStandardItem("(root)")
        root_item.setData("", Qt.ItemDataRole.UserRole)
        root_item.setEditable(False)
        self.tree_model.appendRow(root_item)
        for folder in self.model.folders():
            if not folder:
                continue
            item = QStandardItem(folder)
            item.setData(folder, Qt.ItemDataRole.UserRole)
            item.setEditable(False)
            self.tree_model.appendRow(item)

    def _rebuild_list(self) -> None:
        """Refill the asset list using the current folder, search text and kind filter."""
        self.list_model.removeRows(0, self.list_model.rowCount())
        # hasattr guards cover the first call, which can happen before the controls exist when a subclass or test triggers a rebuild early.
        kind = _KIND_BY_FILTER_LABEL.get(self.kind_filter.currentText() if hasattr(self, "kind_filter") else FILTER_ALL)
        search_text = self.search_box.text() if hasattr(self, "search_box") else ""
        entries = self.model.filter_entries(folder=self._current_folder, search_text=search_text, kind=kind)
        for entry in entries:
            item = QStandardItem(entry.name)
            item.setData(entry.relative_path, Qt.ItemDataRole.UserRole)
            item.setEditable(False)
            item.setIcon(self._icon_for(entry))
            self.list_model.appendRow(item)

    def _icon_for(self, entry: AssetEntry) -> QIcon:
        """Choose the icon for a list row.

        Args:
            entry: the asset.

        Returns:
            A scaled preview for images; for everything else a flat colour swatch with the first three letters of the kind (audio/model/script/video have
            no cheap safe preview without decoding media, which is out of scope -- see THREAT_MODEL.md).
        """
        if entry.kind == AssetKind.IMAGE:
            # Any failure (path escape, unreadable file) silently falls back to the swatch rather than hiding the asset.
            try:
                full_path = self.model.folder_disk_path(_folder_of(entry.relative_path)) / entry.name
                image = QImage(str(full_path))
                # QImage returns a null image for unsupported or corrupt files instead of raising, so check isNull().
                if not image.isNull():
                    scaled = image.scaled(
                        THUMBNAIL_SIZE, THUMBNAIL_SIZE, Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                    return QIcon(QPixmap.fromImage(scaled))
            except (SecurityError, OSError):
                pass
        # Swatch fallback drawn by hand: fill, then draw the kind letters in a dark colour that reads on every swatch colour.
        pixmap = QPixmap(THUMBNAIL_SIZE, THUMBNAIL_SIZE)
        pixmap.fill(QColor(_KIND_PLACEHOLDER_COLORS.get(entry.kind, "#8a8a8a")))
        painter = QPainter(pixmap)
        painter.setPen(QColor("#151515"))
        painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, entry.kind.value[:3].upper())
        painter.end()
        return QIcon(pixmap)

    def _on_folder_selected(self, index) -> None:
        """Show the assets of the folder the user clicked."""
        item = self.tree_model.itemFromIndex(index)
        if item is None:
            return
        self._current_folder = item.data(Qt.ItemDataRole.UserRole) or ""
        self._rebuild_list()

    def _on_filters_changed(self, _value: str) -> None:
        """Rebuild the list when the search text or type filter changes (the argument is the new value, ignored because both controls are re-read)."""
        self._rebuild_list()

    def _selected_relative_path(self, index) -> Optional[str]:
        """Return the relative path stored on a list row, or None."""
        item = self.list_model.itemFromIndex(index)
        if item is None:
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    def _on_asset_double_clicked(self, index) -> None:
        """Emit asset_activated for the double-clicked row."""
        relative_path = self._selected_relative_path(index)
        if relative_path:
            self.asset_activated.emit(relative_path)

    def _on_context_menu(self, position) -> None:
        """Show the right-click menu for the row under the cursor and run the chosen action."""
        # Right-click on empty space: indexAt gives an invalid index, so no path is found and no menu is shown.
        index = self.list_view.indexAt(position)
        relative_path = self._selected_relative_path(index)
        if not relative_path:
            return

        menu = QMenu(self)
        rename_action = menu.addAction("Rename")
        delete_action = menu.addAction("Delete")
        show_action = menu.addAction("Show in Explorer")
        # menu.exec blocks until the user chooses; the position is converted from list coordinates to screen coordinates.
        chosen = menu.exec(self.list_view.viewport().mapToGlobal(position))

        if chosen is rename_action:
            self._rename_asset(relative_path)
        elif chosen is delete_action:
            self._delete_asset(relative_path)
        elif chosen is show_action:
            self._show_in_explorer(relative_path)

    def _rename_asset(self, relative_path: str) -> None:
        """Ask for a new name and rename the file; show a warning dialog if the model refuses."""
        current_name = Path(relative_path).name
        new_name, ok = QInputDialog.getText(self, "Rename Asset", "New name:", text=current_name)
        if not ok or not new_name or new_name == current_name:
            return
        try:
            self.model.rename_asset(relative_path, new_name)
        except SecurityError as exc:
            QMessageBox.warning(self, "Rename Blocked", str(exc))
            return
        self.refresh()

    def _delete_asset(self, relative_path: str) -> None:
        """Confirm, then permanently delete the file; show a warning dialog if the model refuses."""
        confirmed = QMessageBox.question(
            self,
            "Delete Asset",
            f"Permanently delete '{relative_path}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirmed != QMessageBox.StandardButton.Yes:
            return
        try:
            self.model.delete_asset(relative_path)
        except SecurityError as exc:
            QMessageBox.warning(self, "Delete Blocked", str(exc))
            return
        self.refresh()

    def _show_in_explorer(self, relative_path: str) -> None:
        """Open the asset's folder in the operating system's file manager."""
        try:
            # Uses the model's private _resolve so the folder passes the same containment check as other operations.
            folder = self.model._resolve(str(Path(relative_path).parent))
        except SecurityError as exc:
            QMessageBox.warning(self, "Cannot Show Asset", str(exc))
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def set_root(self, root_path: str) -> None:
        """Re-point the browser at a new workspace root and reset to the root folder.

        Called by MainWindow.open_folder (File > Open Folder).
        """
        self.model = AssetManagerModel(root_path)
        self._current_folder = ""
        self.refresh()

    def get_state(self) -> dict:
        """Return the data needed to restore this view: {"root_path", "current_folder"}."""
        return {"root_path": self.model.root_path, "current_folder": self._current_folder}

    def load_state(self, state: dict) -> None:
        """Restore a saved view.

        Args:
            state: dict as produced by get_state; missing keys keep the current root and select the root folder.
        """
        root_path = state.get("root_path")
        if root_path:
            self.model = AssetManagerModel(root_path)
        self._current_folder = state.get("current_folder", "")
        self.refresh()
