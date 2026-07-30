from PySide6.QtWidgets import QTreeView, QFileSystemModel
from pathlib import Path

class FileExplorer(QTreeView):
    def __init__(self, root_path=None, parent=None):
        super().__init__(parent)
        self.model = QFileSystemModel()
        
        self.setModel(self.model)
        
        if root_path is not None:
            self.set_workspace(root_path)
        
        # Hide extra columns to keep it clean (like VS Code)
        for i in range(1, 4):
            self.hideColumn(i)
            
        self.setHeaderHidden(True)
        self.setAnimated(True)
        
    def set_workspace(self, path: str):
        workspace = Path(path).expanduser().resolve(strict=True)
        if not workspace.is_dir():
            raise ValueError("Workspace must be a directory.")
        workspace_path = str(workspace)
        self.model.setRootPath(workspace_path)
        self.setRootIndex(self.model.index(workspace_path))
