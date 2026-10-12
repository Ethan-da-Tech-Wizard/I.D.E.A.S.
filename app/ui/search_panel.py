import os
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton, QListWidget, QListWidgetItem, QLabel
from PySide6.QtCore import Qt, Signal
from app.security import read_text_file_safely, SecurityError

class SearchPanel(QWidget):
    result_selected = Signal(str, int) # (file_path, line_number)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.workspace_path = None
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        
        # Search row
        search_row = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search text in files...")
        self.search_input.returnPressed.connect(self.perform_search)
        
        self.search_btn = QPushButton("Find")
        self.search_btn.clicked.connect(self.perform_search)
        self.search_btn.setStyleSheet("""
            QPushButton {
                background-color: #6c5ce7;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 4px 12px;
                font-weight: bold;
                font-family: 'JetBrains Mono', monospace;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #5b4dbf;
            }
        """)
        
        search_row.addWidget(self.search_input)
        search_row.addWidget(self.search_btn)
        layout.addLayout(search_row)
        
        self.status_lbl = QLabel("")
        self.status_lbl.setStyleSheet("color: #8a8a9a; font-size: 10px; font-family: 'JetBrains Mono', monospace;")
        layout.addWidget(self.status_lbl)
        
        # Search results list
        self.results_list = QListWidget()
        self.results_list.setObjectName("SearchResults")
        self.results_list.itemDoubleClicked.connect(self.on_item_double_clicked)
        self.results_list.setStyleSheet("""
            QListWidget#SearchResults {
                background-color: #050508;
                border: 1px solid #1a1a28;
                color: #d0d0d8;
                font-family: 'JetBrains Mono', monospace;
                font-size: 11px;
            }
            QListWidget#SearchResults::item {
                padding: 6px;
                border-bottom: 1px solid #101018;
            }
            QListWidget#SearchResults::item:selected {
                background-color: rgba(108, 92, 231, 0.25);
                color: #ffffff;
                border-left: 2px solid #6c5ce7;
            }
        """)
        layout.addWidget(self.results_list, 1)

    def set_workspace(self, path):
        self.workspace_path = path
        self.results_list.clear()
        self.status_lbl.setText("")

    def perform_search(self):
        query = self.search_input.text().strip()
        self.results_list.clear()
        if not self.workspace_path or not query:
            self.status_lbl.setText("No active workspace or empty query.")
            return
            
        self.status_lbl.setText("Searching workspace...")
        matches = []
        
        for root, dirs, files in os.walk(self.workspace_path):
            # Exclude hidden folders (.git, etc.)
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            for file in files:
                if file.startswith('.'):
                    continue
                file_path = os.path.join(root, file)
                
                # Check extension is readable text
                ext = os.path.splitext(file)[1].lower()
                if ext not in ('.py', '.md', '.txt', '.json', '.xml', '.qss', '.html', '.css', '.js'):
                    continue
                    
                try:
                    content = read_text_file_safely(file_path)
                    lines = content.splitlines()
                    for idx, line in enumerate(lines):
                        if query.lower() in line.lower():
                            matches.append((file_path, idx + 1, line.strip()))
                            if len(matches) >= 200:
                                break
                except SecurityError:
                    continue
                except Exception:
                    continue
                    
                if len(matches) >= 200:
                    break
            if len(matches) >= 200:
                break
                
        self.status_lbl.setText(f"Found {len(matches)} match(es)" + (" (capped)" if len(matches) >= 200 else ""))
        
        for path, line_no, text in matches:
            rel_path = os.path.relpath(path, self.workspace_path)
            item_text = f"{rel_path}:{line_no} - {text}"
            item = QListWidgetItem(item_text)
            item.setData(Qt.ItemDataRole.UserRole, (path, line_no))
            self.results_list.addItem(item)

    def on_item_double_clicked(self, item):
        data = item.data(Qt.ItemDataRole.UserRole)
        if data:
            file_path, line_no = data
            self.result_selected.emit(file_path, line_no)
