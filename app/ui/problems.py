from PySide6.QtWidgets import QTableWidget, QTableWidgetItem, QHeaderView
from PySide6.QtCore import Qt, Signal

class ProblemsWidget(QTableWidget):
    row_double_clicked = Signal(str, int) # (file_path, line_number)
    
    def __init__(self, parent=None):
        super().__init__(0, 4, parent)
        self.setObjectName("ProblemsWidget")
        self.setHorizontalHeaderLabels(["Severity", "Message", "File", "Line"])
        
        # Configure headers
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.horizontalHeader().setStretchLastSection(True)
        self.verticalHeader().setVisible(False)
        
        self.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.itemDoubleClicked.connect(self.on_item_double_clicked)
        
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
        self.setRowCount(0)
        for prob in problems_list:
            row = self.rowCount()
            self.insertRow(row)
            
            sev_item = QTableWidgetItem("🔴 Syntax Error")
            msg_item = QTableWidgetItem(prob.get("msg", ""))
            file_item = QTableWidgetItem(prob.get("file", ""))
            line_item = QTableWidgetItem(str(prob.get("lineno", 1)))
            
            # Embed metadata for double-click jumping
            sev_item.setData(Qt.ItemDataRole.UserRole, (prob.get("file", ""), prob.get("lineno", 1)))
            
            self.setItem(row, 0, sev_item)
            self.setItem(row, 1, msg_item)
            self.setItem(row, 2, file_item)
            self.setItem(row, 3, line_item)

    def on_item_double_clicked(self, item):
        row = item.row()
        sev_item = self.item(row, 0)
        if sev_item:
            data = sev_item.data(Qt.ItemDataRole.UserRole)
            if data:
                file_path, line = data
                self.row_double_clicked.emit(file_path, line)
