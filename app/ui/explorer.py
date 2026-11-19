# ==============================================================================
# Program: app/ui/explorer.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: the file-tree sidebar for the open workspace (a QTreeView over QFileSystemModel) with a
# right-click menu to create, rename and delete files and folders and to reveal them in the OS file
# manager. Every mutation first checks the target is inside the workspace (is_safe_subpath).
# Used by: app/application.py (set_workspace when a folder opens; the view's own clicks are wired
# there to open files).
# Design: a QFileSystemWatcher on the workspace root triggers a model refresh when entries
# appear or disappear outside the app.
# Known limits / bugs noticed: (1) `self.model = QFileSystemModel()` shadows QTreeView.model(), so
# the inherited accessor no longer works on this widget; (2) the watcher only watches the root
# folder, not subfolders; (3) rename/delete use plain os calls with no undo, delete asks only once;
# (4) on_directory_changed forces a refresh by resetting the root path to "" and back.
# ==============================================================================

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
        """Create the tree, hide the extra file-model columns and install the context menu and watcher.

        Args:
            root_path: optional workspace folder to show immediately.
            parent: optional Qt parent widget.
        """
        super().__init__(parent)
        self.model = QFileSystemModel()
        self.workspace_path = None

        self.setModel(self.model)

        # Set the workspace now when one is known; otherwise the tree stays empty until set_workspace.
        if root_path is not None:
            self.set_workspace(root_path)
        
        # The file model has Name, Size, Type and Date columns; only the name (column 0) is wanted.
        # Hide columns to keep view simple
        for i in range(1, 4):
            self.hideColumn(i)
            
        self.setHeaderHidden(True)
        # Smooth expand/collapse.
        self.setAnimated(True)
        
        # CustomContextMenu makes Qt emit customContextMenuRequested instead of showing a default menu.
        # Right-click context menu
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self.show_context_menu)
        
        # Parent is self so the watcher is destroyed with the widget.
        # Directory changes watcher
        self.watcher = QFileSystemWatcher(self)
        self.watcher.directoryChanged.connect(self.on_directory_changed)

    def set_workspace(self, path: str):
        """Show `path` as the root of the tree and watch it for changes.

        Args:
            path: workspace folder (a leading ~ is expanded).

        Raises:
            FileNotFoundError: path does not exist (resolve(strict=True)).
            ValueError: path exists but is not a directory.
        """
        # resolve(strict=True) turns symlinks and ".." into a real absolute path; the stored workspace_path is later the anchor for all "inside the workspace" checks.
        workspace = Path(path).expanduser().resolve(strict=True)
        if not workspace.is_dir():
            raise ValueError("Workspace must be a directory.")
        workspace_path = str(workspace)
        # Tells the model to start loading and watching this folder.
        self.model.setRootPath(workspace_path)
        self.setRootIndex(self.model.index(workspace_path))
        self.workspace_path = workspace_path
        
        # Replace any previous watch so only the current workspace is observed.
        # Monitor directory
        existing = self.watcher.directories()
        if existing:
            self.watcher.removePaths(existing)
        self.watcher.addPath(workspace_path)

    def on_directory_changed(self, path):
        """Refresh the tree after the watcher reports a change.

        Args:
            path: changed directory reported by Qt (unused; the whole root is refreshed).
        """
        # Qt's model can miss external changes; clearing and re-setting the root path makes it rescan.
        # Force refresh tree
        self.model.setRootPath("")
        self.model.setRootPath(self.workspace_path)

    def show_context_menu(self, point):
        """Build and run the right-click menu, then dispatch the chosen action.

        Args:
            point: click position in viewport coordinates.
        """
        # Invalid index means the user clicked empty space (the workspace itself is then the target).
        index = self.indexAt(point)
        menu = QMenu(self)
        
        new_file_act = menu.addAction("New File")
        new_folder_act = menu.addAction("New Folder")
        menu.addSeparator()
        
        # Rename/Delete only make sense on an existing item; on empty space the menu offers "Reveal Workspace".
        if index.isValid():
            rename_act = menu.addAction("Rename")
            delete_act = menu.addAction("Delete")
            menu.addSeparator()
            reveal_act = menu.addAction("Reveal in File Explorer")
        else:
            rename_act = None
            delete_act = None
            reveal_act = menu.addAction("Reveal Workspace in OS")

        # exec blocks until the user picks something; mapToGlobal converts to screen coordinates for the popup.
        action = menu.exec(self.viewport().mapToGlobal(point))
        
        if index.isValid():
            path = self.model.filePath(index)
        else:
            path = self.workspace_path

        # No workspace and no item: nothing to act on.
        if not path:
            return

        # The action objects are compared by identity; rename_act/delete_act are None on empty space, which never equals a real action.
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
        """Ask for a name and create an empty file next to (or inside) `parent_path`.

        Args:
            parent_path: clicked file (its folder is used) or folder.
        """
        # Creating "in" a file means creating beside it.
        if os.path.isfile(parent_path):
            parent_dir = os.path.dirname(parent_path)
        else:
            parent_dir = parent_path
            
        name, ok = QInputDialog.getText(self, "New File", "Enter file name:")
        if ok and name.strip():
            # abspath normalises ".." so a name like "../x" is detected by the containment check.
            target_path = os.path.abspath(os.path.join(parent_dir, name.strip()))
            try:
                # Refuse names that would land outside the workspace.
                if not is_safe_subpath(self.workspace_path, target_path):
                    raise SecurityError("Cannot escape workspace directory.")
                # Opening with "w" would truncate an existing file with the same name (known limit: no overwrite warning).
                with open(target_path, "w", encoding="utf-8") as f:
                    f.write("")
            # Any failure is shown to the user instead of crashing the UI.
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to create file: {e}")

    def create_new_folder(self, parent_path):
        """Ask for a name and create a folder (and parents) beside/inside `parent_path`; same containment check as create_new_file.

        Args:
            parent_path: clicked file or folder.
        """
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
                # exist_ok=True: creating an existing folder is not an error.
                os.makedirs(target_path, exist_ok=True)
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to create folder: {e}")

    def rename_item(self, path, index):
        """Rename `path` within its own folder after a containment check.

        Args:
            path: item to rename.
            index: model index of the item (currently unused).
        """
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
        """Delete a file or a whole folder tree after a confirmation dialog.

        Args:
            path: item to delete; must be inside the workspace.
        """
        btn = QMessageBox.question(self, "Confirm Delete", f"Are you sure you want to delete '{os.path.basename(path)}'?")
        # Only an explicit Yes deletes.
        if btn == QMessageBox.StandardButton.Yes:
            try:
                if not is_safe_subpath(self.workspace_path, path):
                    raise SecurityError("Cannot escape workspace directory.")
                if os.path.isdir(path):
                    # Imported lazily: only needed for folder deletion.
                    import shutil
                    # Irreversible recursive delete; the containment check above is what keeps it away from the rest of the disk.
                    shutil.rmtree(path)
                else:
                    os.remove(path)
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to delete: {e}")

    def reveal_in_os(self, path):
        """Open the item's folder in the system file manager.

        Args:
            path: file or folder; a file is replaced by its parent folder.
        """
        if os.path.isfile(path):
            path = os.path.dirname(path)
        # Asks the OS to open a file:// location with the default handler (no network involved).
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))
