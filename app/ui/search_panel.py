# ==============================================================================
# Program: app/ui/search_panel.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: the "Search" sidebar tab: a find-in-files box. It walks the open workspace folder, reads
# every text-like file, and lists lines containing the query (case-insensitive substring, no
# regular expressions). Double-clicking a result emits result_selected(file_path, line_number) so
# the main window can open the file at that line.
# Used by: app/application.py (set_workspace is called when a folder opens; result_selected is
# connected to the editor navigation).
# Limits: at most 200 matches (the loop stops early and the status line says "capped"); only the
# extensions listed in perform_search are read; hidden files and folders (leading ".") are skipped;
# the search runs on the UI thread, so a very large workspace can briefly freeze the window.
# Safety: files are read through read_text_file_safely (size cap, binary rejection); unreadable
# files are skipped silently.
# ==============================================================================

import os
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton, QListWidget, QListWidgetItem, QLabel
from PySide6.QtCore import Qt, Signal
from app.security import read_text_file_safely, SecurityError

class SearchPanel(QWidget):
    """Find-in-files widget: query box, Find button, status line and a results list.

    Signals:
        result_selected(str, int): absolute file path and 1-based line number of the chosen match.
    """
    result_selected = Signal(str, int) # (file_path, line_number)
    
    def __init__(self, parent=None):
        """Build the UI. No workspace is set yet, so searching shows a hint until set_workspace is called.

        Args:
            parent: optional Qt parent widget.
        """
        super().__init__(parent)
        # None means "no folder open"; perform_search checks it before walking the disk.
        self.workspace_path = None
        
        layout = QVBoxLayout(self)
        # 10 px padding and 8 px spacing give the panel breathing room inside the sidebar.
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        
        # Query field and button share one row; Enter and the button trigger the same search.
        # Search row
        search_row = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search text in files...")
        self.search_input.returnPressed.connect(self.perform_search)
        
        self.search_btn = QPushButton("Find")
        self.search_btn.clicked.connect(self.perform_search)
        # Accent purple (#6c5ce7) button, darker on hover, matching the DAW palette.
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
        
        # Small grey line for "Searching...", counts and the empty-workspace hint.
        self.status_lbl = QLabel("")
        self.status_lbl.setStyleSheet("color: #8a8a9a; font-size: 10px; font-family: 'JetBrains Mono', monospace;")
        layout.addWidget(self.status_lbl)
        
        # objectName "SearchResults" is the selector used by the stylesheet below (dark list, purple selection bar).
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
        # Stretch factor 1: the results take all remaining vertical space.
        layout.addWidget(self.results_list, 1)

    def set_workspace(self, path):
        """Point the panel at a folder and clear any previous results.

        Args:
            path: absolute workspace folder path.
        """
        self.workspace_path = path
        self.results_list.clear()
        self.status_lbl.setText("")

    def perform_search(self):
        """Run the search for the current query text and fill the results list.

        Does nothing but show a hint if there is no workspace or the query is blank. Results are
        (relative path):(line) - (trimmed line text); the absolute path and line are stored on each item for navigation.
        """
        # Surrounding whitespace is ignored; the match itself is case-insensitive (see below).
        query = self.search_input.text().strip()
        self.results_list.clear()
        if not self.workspace_path or not query:
            self.status_lbl.setText("No active workspace or empty query.")
            return
            
        self.status_lbl.setText("Searching workspace...")
        # Tuples of (absolute path, 1-based line number, stripped line).
        matches = []
        
        # Walk the whole tree; `dirs` is edited in place below to prune the walk.
        for root, dirs, files in os.walk(self.workspace_path):
            # Assigning to dirs[:] (not a new list) is what makes os.walk skip those folders: avoids .git and similar large or irrelevant trees.
            # Exclude hidden folders (.git, etc.)
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            for file in files:
                if file.startswith('.'):
                    continue
                file_path = os.path.join(root, file)
                
                # Whitelist of text formats; skipping everything else avoids reading images, audio and binaries.
                # Check extension is readable text
                ext = os.path.splitext(file)[1].lower()
                if ext not in ('.py', '.md', '.txt', '.json', '.xml', '.qss', '.html', '.css', '.js'):
                    continue
                    
                try:
                    # The shared safe reader enforces the size limit and rejects binary content.
                    content = read_text_file_safely(file_path)
                    lines = content.splitlines()
                    for idx, line in enumerate(lines):
                        # Case-insensitive substring match; lower() is recomputed per line for simplicity.
                        if query.lower() in line.lower():
                            matches.append((file_path, idx + 1, line.strip()))
                            # Cap applied at three levels (line, file, directory loop) so the search stops quickly once full.
                            if len(matches) >= 200:
                                break
                # Oversized or binary files are expected; skip them without telling the user.
                except SecurityError:
                    continue
                # Any other read problem (permissions, encoding) must not abort the whole search.
                except Exception:
                    continue
                    
                if len(matches) >= 200:
                    break
            if len(matches) >= 200:
                break
                
        # Shows "(capped)" when the limit was hit, since there may be more matches than listed.
        self.status_lbl.setText(f"Found {len(matches)} match(es)" + (" (capped)" if len(matches) >= 200 else ""))
        
        for path, line_no, text in matches:
            # Relative paths keep list rows short and readable.
            rel_path = os.path.relpath(path, self.workspace_path)
            item_text = f"{rel_path}:{line_no} - {text}"
            item = QListWidgetItem(item_text)
            # Keep the absolute path and line with the row for on_item_double_clicked.
            item.setData(Qt.ItemDataRole.UserRole, (path, line_no))
            self.results_list.addItem(item)

    def on_item_double_clicked(self, item):
        """Emit result_selected for the double-clicked result.

        Args:
            item: the QListWidgetItem whose UserRole holds (file_path, line_no).
        """
        data = item.data(Qt.ItemDataRole.UserRole)
        if data:
            file_path, line_no = data
            self.result_selected.emit(file_path, line_no)
