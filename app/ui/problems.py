# ==============================================================================
# Program: app/ui/problems.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: the "Problems" panel: a read-only table listing code errors found in the open file
# (severity, message, file, line). Double-clicking a row emits row_double_clicked(file, line) so
# the main window can jump the editor to that spot.
# Used by: app/application.py (creates one, feeds it with update_problems, connects the signal to
# navigate_to_problem). Input rows are dicts with keys "msg", "file" and "lineno" (others such as
# "col"/"end_col" may be present and are ignored here).
# Known limit: every row is labelled "Syntax Error" because the only producer today is the Python
# syntax check; add a "severity" key and read it here when other kinds of problems exist.
# Tested by: tests/test_ide_core.py (test_problems_widget).
# ==============================================================================

from PySide6.QtWidgets import QTableWidget, QTableWidgetItem, QHeaderView
from PySide6.QtCore import Qt, Signal

class ProblemsWidget(QTableWidget):
    """Table of problems with four columns: Severity, Message, File, Line.

    Signals:
        row_double_clicked(str, int): file path and 1-based line number of the double-clicked row.
    """
    row_double_clicked = Signal(str, int) # (file_path, line_number)
    
    def __init__(self, parent=None):
        """Create an empty four-column table and apply its look.

        Args:
            parent: optional Qt parent widget.
        """
        # 0 rows to start with, 4 columns (see header labels).
        super().__init__(0, 4, parent)
        # Object name is used by the stylesheet selector below and by tests/themes to find the widget.
        self.setObjectName("ProblemsWidget")
        self.setHorizontalHeaderLabels(["Severity", "Message", "File", "Line"])
        
        # Interactive columns can be resized by the user; the last column stretches to fill the width.
        # Configure headers
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.horizontalHeader().setStretchLastSection(True)
        # Row numbers would only add noise next to the Line column.
        self.verticalHeader().setVisible(False)
        
        # Read-only: cells are information, not inputs.
        self.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        # Whole-row selection with a single row at a time matches the "jump to this problem" action.
        self.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        # Qt reports the double-clicked cell; on_item_double_clicked converts it to a row-level signal.
        self.itemDoubleClicked.connect(self.on_item_double_clicked)
        
        # Colours follow the DAW palette (very dark background, purple hover tint rgba(108, 92, 231, 0.1)).
        # Sleek dark stylesheet matching the DAW palette
        self.setStyleSheet("""
            QTableWidget#ProblemsWidget {
                background-color: #050508;
                gridline-color: #1a1a28;
                color: #d0d0d8;
                border: none;
                font-family: 'JetBrains Mono', monospace;
                font-size: 11px;
            }
            QTableWidget::item {
                padding: 4px;
                border-bottom: 1px solid #101018;
            }
            QTableWidget::item:hover {
                background-color: rgba(108, 92, 231, 0.1);
            }
            QHeaderView::section {
                background-color: #12121a;
                color: #8a8a9a;
                border: 1px solid #1a1a28;
                padding: 5px;
                font-weight: bold;
                font-size: 11px;
            }
        """)

    def update_problems(self, problems_list):
        """Replace the table contents with `problems_list`.

        Args:
            problems_list: iterable of dicts with optional keys "msg", "file", "lineno" (default line 1).
        """
        # Clear all rows first; cheaper and simpler than diffing.
        self.setRowCount(0)
        for prob in problems_list:
            row = self.rowCount()
            self.insertRow(row)
            
            # Fixed label for now (see "Known limit" in the file header).
            sev_item = QTableWidgetItem("🔴 Syntax Error")
            msg_item = QTableWidgetItem(prob.get("msg", ""))
            file_item = QTableWidgetItem(prob.get("file", ""))
            line_item = QTableWidgetItem(str(prob.get("lineno", 1)))
            
            # Store (file, line) as data on the first cell so the double-click handler does not have to parse text.
            # Embed metadata for double-click jumping
            sev_item.setData(Qt.ItemDataRole.UserRole, (prob.get("file", ""), prob.get("lineno", 1)))
            
            self.setItem(row, 0, sev_item)
            self.setItem(row, 1, msg_item)
            self.setItem(row, 2, file_item)
            self.setItem(row, 3, line_item)

    def on_item_double_clicked(self, item):
        """Translate a cell double-click into row_double_clicked(file, line) using the data stored on column 0.

        Args:
            item: the QTableWidgetItem that was double-clicked (any column).
        """
        row = item.row()
        # The data lives on column 0 regardless of which cell was clicked.
        sev_item = self.item(row, 0)
        if sev_item:
            data = sev_item.data(Qt.ItemDataRole.UserRole)
            # A missing payload (for example an empty row) is silently ignored.
            if data:
                file_path, line = data
                self.row_double_clicked.emit(file_path, line)
