import os
from PySide6.QtWidgets import QTreeView, QFileSystemModel, QMenu, QInputDialog, QMessageBox
from PySide6.QtCore import Qt, QDir, QFileSystemWatcher, QUrl
from PySide6.QtGui import QDesktopServices
from pathlib import Path
from app.security import SecurityError
from app.storage.workspace import is_safe_subpath

class FileExplorer(QTreeView):
    """File explorer listing workspace contents with right-click actions and dynamic watchers."""

    def __init__(self, root_path=None, parent=None):
        super().__init__(parent)
        self.model = QFileSystemModel()
        self.workspace_path = None

        self.setModel(self.model)

        if root_path is not None:
            self.set_workspace(root_path)
        
        # Hide columns to keep view simple
        for i in range(1, 4):
            self.hideColumn(i)
            
        self.setHeaderHidden(True)
        self.setAnimated(True)
        
        # Right-click context menu
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self.show_context_menu)
        
        # Directory changes watcher
        self.watcher = QFileSystemWatcher(self)
        self.watcher.directoryChanged.connect(self.on_directory_changed)

    def set_workspace(self, path: str):
        workspace = Path(path).expanduser().resolve(strict=True)
        if not workspace.is_dir():
            raise ValueError("Workspace must be a directory.")
        workspace_path = str(workspace)
        self.model.setRootPath(workspace_path)
        self.setRootIndex(self.model.index(workspace_path))
        self.workspace_path = workspace_path
        
        # Monitor directory
        existing = self.watcher.directories()
        if existing:
            self.watcher.removePaths(existing)
        self.watcher.addPath(workspace_path)

    def on_directory_changed(self, path):
        # Force refresh tree
        self.model.setRootPath("")
        self.model.setRootPath(self.workspace_path)

    def show_context_menu(self, point):
        index = self.indexAt(point)
        menu = QMenu(self)
        
        new_file_act = menu.addAction("New File")
        new_folder_act = menu.addAction("New Folder")
        menu.addSeparator()
        
        if index.isValid():
            rename_act = menu.addAction("Rename")
            delete_act = menu.addAction("Delete")
            menu.addSeparator()
            reveal_act = menu.addAction("Reveal in File Explorer")
        else:
            rename_act = None
            delete_act = None
            reveal_act = menu.addAction("Reveal Workspace in OS")

        action = menu.exec(self.viewport().mapToGlobal(point))
        
        if index.isValid():
            path = self.model.filePath(index)
        else:
            path = self.workspace_path

        if not path:
            return

        if action == new_file_act:
            self.create_new_file(path)
        elif action == new_folder_act:
            self.create_new_folder(path)
        elif action == rename_act:
            self.rename_item(path, index)
        elif action == delete_act:
            self.delete_item(path)
        elif action == reveal_act:
            self.reveal_in_os(path)

    def create_new_file(self, parent_path):
        if os.path.isfile(parent_path):
            parent_dir = os.path.dirname(parent_path)
        else:
            parent_dir = parent_path
            
        name, ok = QInputDialog.getText(self, "New File", "Enter file name:")
        if ok and name.strip():
            target_path = os.path.abspath(os.path.join(parent_dir, name.strip()))
            try:
                if not is_safe_subpath(self.workspace_path, target_path):
                    raise SecurityError("Cannot escape workspace directory.")
                with open(target_path, "w", encoding="utf-8") as f:
                    f.write("")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to create file: {e}")

    def create_new_folder(self, parent_path):
        if os.path.isfile(parent_path):
            parent_dir = os.path.dirname(parent_path)
        else:
            parent_dir = parent_path
            
        name, ok = QInputDialog.getText(self, "New Folder", "Enter folder name:")
        if ok and name.strip():
            target_path = os.path.abspath(os.path.join(parent_dir, name.strip()))
            try:
                if not is_safe_subpath(self.workspace_path, target_path):
                    raise SecurityError("Cannot escape workspace directory.")
                os.makedirs(target_path, exist_ok=True)
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to create folder: {e}")

    def rename_item(self, path, index):
        name, ok = QInputDialog.getText(self, "Rename", "Enter new name:", text=os.path.basename(path))
        if ok and name.strip():
            target_path = os.path.abspath(os.path.join(os.path.dirname(path), name.strip()))
            try:
                if not is_safe_subpath(self.workspace_path, target_path):
                    raise SecurityError("Cannot escape workspace directory.")
                os.rename(path, target_path)
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to rename: {e}")

    def delete_item(self, path):
        btn = QMessageBox.question(self, "Confirm Delete", f"Are you sure you want to delete '{os.path.basename(path)}'?")
        if btn == QMessageBox.StandardButton.Yes:
            try:
                if not is_safe_subpath(self.workspace_path, path):
                    raise SecurityError("Cannot escape workspace directory.")
                if os.path.isdir(path):
                    import shutil
                    shutil.rmtree(path)
                else:
                    os.remove(path)
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to delete: {e}")

    def reveal_in_os(self, path):
        if os.path.isfile(path):
            path = os.path.dirname(path)
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))
