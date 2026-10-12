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

FILTER_ALL = "All"
_KIND_BY_FILTER_LABEL: dict[str, Optional[AssetKind]] = {FILTER_ALL: None}
for _kind in AssetKind:
    _KIND_BY_FILTER_LABEL[_kind.value.capitalize()] = _kind

THUMBNAIL_SIZE = 48

_KIND_PLACEHOLDER_COLORS: dict[AssetKind, str] = {
    AssetKind.AUDIO: "#3ddc97",
    AssetKind.IMAGE: "#00f0ff",
    AssetKind.MODEL: "#a26bfa",
    AssetKind.SCRIPT: "#ffd23f",
    AssetKind.VIDEO: "#ff5f1f",
}

ASSET_MIME_TYPE = "application/x-ideas-asset"


def encode_asset_mime_payload(relative_path: str) -> bytes:
    """Pure encode step for drag-and-drop: the MIME payload is just the
    asset's UTF-8 relative path, so any widget that receives a drop can
    forward it straight into the same routing MainWindow._route_asset_activation()
    already uses for double-click (see AssetManagerWidget.asset_activated)."""
    if not relative_path:
        raise SecurityError("Cannot drag an empty asset path.")
    return relative_path.encode("utf-8")


def decode_asset_mime_payload(data: bytes) -> str:
    """Inverse of encode_asset_mime_payload(); raises SecurityError on
    anything malformed rather than handing a target widget garbage."""
    try:
        relative_path = bytes(data).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SecurityError("Dropped asset payload is not valid UTF-8.") from exc
    if not relative_path:
        raise SecurityError("Dropped asset payload is empty.")
    return relative_path


def _folder_of(relative_path: str) -> str:
    parent = str(Path(relative_path).parent)
    return "" if parent == "." else parent


def kind_for_relative_path(relative_path: str) -> Optional[AssetKind]:
    """Pure lookup: file suffix -> AssetKind, used to route an activated asset
    to the right editor (Photo/Video/DAW) without needing a live catalog."""
    return ALLOWED_ASSET_SUFFIXES.get(Path(relative_path).suffix.lower())


class AssetManagerModel:
    """Pure-Python catalog wrapper: search/kind filtering and validated file ops."""

    def __init__(self, root_path: str) -> None:
        self.root_path = os.path.abspath(root_path)
        self.catalog: AssetCatalog = build_asset_catalog(self.root_path)

    def refresh(self) -> None:
        self.catalog = build_asset_catalog(self.root_path)

    def folders(self) -> list[str]:
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
        candidate = (Path(self.root_path) / relative_path).resolve(strict=False)
        if not is_safe_subpath(self.root_path, str(candidate)):
            raise SecurityError("Asset path escapes the workspace root.")
        return candidate

    def rename_asset(self, relative_path: str, new_name: str) -> str:
        if not new_name or any(sep in new_name for sep in ("/", "\\")) or new_name in {".", ".."}:
            raise SecurityError("Invalid asset name.")
        source = self._resolve(relative_path)
        if not source.is_file():
            raise SecurityError("Asset does not exist.")
        destination = self._resolve(str(Path(relative_path).parent / new_name))
        if destination.exists():
            raise SecurityError("An asset with that name already exists.")
        source.rename(destination)
        new_relative_path = str((Path(relative_path).parent / new_name)).replace(os.sep, "/")
        self.refresh()
        return new_relative_path

    def delete_asset(self, relative_path: str) -> None:
        target = self._resolve(relative_path)
        if not target.is_file():
            raise SecurityError("Asset does not exist.")
        target.unlink()
        self.refresh()

    def folder_disk_path(self, folder: str) -> Path:
        return self._resolve(folder) if folder else Path(self.root_path)


class AssetListModel(QStandardItemModel):
    """QStandardItemModel that exposes a custom drag MIME type (ASSET_MIME_TYPE)
    carrying the asset's relative path, instead of Qt's default internal
    item-data-list format, so any widget can accept the drop without knowing
    anything about QStandardItemModel's encoding."""

    def mimeTypes(self) -> list[str]:  # noqa: N802 - Qt override
        return [ASSET_MIME_TYPE]

    def mimeData(self, indexes) -> QMimeData:  # noqa: N802 - Qt override
        mime = QMimeData()
        for index in indexes:
            if not index.isValid():
                continue
            relative_path = index.data(Qt.ItemDataRole.UserRole)
            if relative_path:
                mime.setData(ASSET_MIME_TYPE, encode_asset_mime_payload(relative_path))
                break
        return mime


class AssetManagerWidget(QWidget):
    """Browses a workspace's assets: folder tree, filtered list, search, and actions."""

    asset_activated = Signal(str)

    def __init__(self, root_path: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("AssetManagerWidget")
        self.model = AssetManagerModel(root_path)
        self._current_folder = ""
        self.setup_ui()
        self.refresh()

    def setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

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
        self.list_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list_view.customContextMenuRequested.connect(self._on_context_menu)
        self.list_view.doubleClicked.connect(self._on_asset_double_clicked)
        splitter.addWidget(self.list_view)

        splitter.setSizes([200, 500])
        layout.addWidget(splitter, 1)

    def refresh(self) -> None:
        self.model.refresh()
        self._rebuild_tree()
        self._rebuild_list()

    def _rebuild_tree(self) -> None:
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
        self.list_model.removeRows(0, self.list_model.rowCount())
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
        """Real scaled-down preview for images; a flat color-coded swatch for
        everything else (audio/model/script/video have no cheap safe preview
        without decoding media, which is out of scope -- see THREAT_MODEL.md)."""
        if entry.kind == AssetKind.IMAGE:
            try:
                full_path = self.model.folder_disk_path(_folder_of(entry.relative_path)) / entry.name
                image = QImage(str(full_path))
                if not image.isNull():
                    scaled = image.scaled(
                        THUMBNAIL_SIZE, THUMBNAIL_SIZE, Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                    return QIcon(QPixmap.fromImage(scaled))
            except (SecurityError, OSError):
                pass
        pixmap = QPixmap(THUMBNAIL_SIZE, THUMBNAIL_SIZE)
        pixmap.fill(QColor(_KIND_PLACEHOLDER_COLORS.get(entry.kind, "#8a8a8a")))
        painter = QPainter(pixmap)
        painter.setPen(QColor("#151515"))
        painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, entry.kind.value[:3].upper())
        painter.end()
        return QIcon(pixmap)

    def _on_folder_selected(self, index) -> None:
        item = self.tree_model.itemFromIndex(index)
        if item is None:
            return
        self._current_folder = item.data(Qt.ItemDataRole.UserRole) or ""
        self._rebuild_list()

    def _on_filters_changed(self, _value: str) -> None:
        self._rebuild_list()

    def _selected_relative_path(self, index) -> Optional[str]:
        item = self.list_model.itemFromIndex(index)
        if item is None:
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    def _on_asset_double_clicked(self, index) -> None:
        relative_path = self._selected_relative_path(index)
        if relative_path:
            self.asset_activated.emit(relative_path)

    def _on_context_menu(self, position) -> None:
        index = self.list_view.indexAt(position)
        relative_path = self._selected_relative_path(index)
        if not relative_path:
            return

        menu = QMenu(self)
        rename_action = menu.addAction("Rename")
        delete_action = menu.addAction("Delete")
        show_action = menu.addAction("Show in Explorer")
        chosen = menu.exec(self.list_view.viewport().mapToGlobal(position))

        if chosen is rename_action:
            self._rename_asset(relative_path)
        elif chosen is delete_action:
            self._delete_asset(relative_path)
        elif chosen is show_action:
            self._show_in_explorer(relative_path)

    def _rename_asset(self, relative_path: str) -> None:
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
        try:
            folder = self.model._resolve(str(Path(relative_path).parent))
        except SecurityError as exc:
            QMessageBox.warning(self, "Cannot Show Asset", str(exc))
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def set_root(self, root_path: str) -> None:
        """Repoints the browser at a new workspace root (e.g. after the user
        picks a project folder via File > Open Folder)."""
        self.model = AssetManagerModel(root_path)
        self._current_folder = ""
        self.refresh()

    def get_state(self) -> dict:
        return {"root_path": self.model.root_path, "current_folder": self._current_folder}

    def load_state(self, state: dict) -> None:
        root_path = state.get("root_path")
        if root_path:
            self.model = AssetManagerModel(root_path)
        self._current_folder = state.get("current_folder", "")
        self.refresh()
