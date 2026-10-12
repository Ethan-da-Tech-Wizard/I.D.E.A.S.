import re
from PySide6.QtWidgets import QFrame, QListWidget, QListWidgetItem, QHBoxLayout, QTextEdit
from PySide6.QtCore import Qt, QRect, QPoint
from PySide6.QtGui import QTextCursor

# Built-in APIs in Safe Mode
BUILTINS_DOC = {
    "play_sample": {
        "sig": "play_sample(name: str)",
        "doc": "Plays a WAV sample bundled within the NeonDAW resources.\nExample:\n  play_sample('kick.wav')"
    },
    "print": {
        "sig": "print(value: str)",
        "doc": "Prints text or variable string representations to the bottom console panel.\nExample:\n  print('Hello from Safe Script!')"
    }
}

class AutocompletePopup(QFrame):
    def __init__(self, editor):
        super().__init__(editor, Qt.WindowFlags.Popup)
        self.editor = editor
        self.setObjectName("AutocompletePopup")
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        self.list_widget = QListWidget(self)
        self.list_widget.setObjectName("AutocompleteList")
        self.list_widget.itemSelectionChanged.connect(self.on_selection_changed)
        self.list_widget.itemDoubleClicked.connect(self.complete_text)
        layout.addWidget(self.list_widget, 1)
        
        self.doc_widget = QTextEdit(self)
        self.doc_widget.setObjectName("AutocompleteDoc")
        self.doc_widget.setReadOnly(True)
        self.doc_widget.setFixedWidth(240)
        layout.addWidget(self.doc_widget, 1)
        
        self.setFixedSize(480, 160)
        
        # Cyberpunk style
        self.setStyleSheet("""
            QFrame#AutocompletePopup {
                background-color: #12121a;
                border: 1px solid #6c5ce7;
                border-radius: 6px;
            }
            QListWidget#AutocompleteList {
                background-color: #12121a;
                border: none;
                color: #d0d0d8;
                font-family: 'JetBrains Mono', monospace;
                font-size: 11px;
                outline: none;
            }
            QListWidget#AutocompleteList::item {
                padding: 4px;
            }
            QListWidget#AutocompleteList::item:selected {
                background-color: rgba(108, 92, 231, 0.25);
                color: #ffffff;
                border-left: 2px solid #6c5ce7;
            }
            QTextEdit#AutocompleteDoc {
                background-color: #0d0d14;
                border: none;
                border-left: 1px solid #1a1a28;
                color: #a0a0c0;
                font-family: 'JetBrains Mono', monospace;
                font-size: 11px;
                padding: 5px;
            }
        """)

    def update_completions(self, prefix):
        self.list_widget.clear()
        
        # Collect built-ins
        items = []
        for name, info in BUILTINS_DOC.items():
            if name.startswith(prefix):
                items.append((name, "builtin", info["sig"], info["doc"]))
                
        # Scan current file symbols
        code = self.editor.toPlainText()
        functions = re.findall(r"def\s+([A-Za-z_][A-Za-z0-9_]*)", code)
        classes = re.findall(r"class\s+([A-Za-z_][A-Za-z0-9_]*)", code)
        variables = re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*=[^=]", code)
        
        for name in sorted(list(set(functions))):
            if name.startswith(prefix) and name not in BUILTINS_DOC:
                items.append((name, "function", f"def {name}()", f"Custom function defined in this script."))
                
        for name in sorted(list(set(classes))):
            if name.startswith(prefix):
                items.append((name, "class", f"class {name}", f"Custom class defined in this script."))
                
        for name in sorted(list(set(variables))):
            if name.startswith(prefix) and name not in BUILTINS_DOC and name not in functions and name not in classes:
                items.append((name, "variable", name, f"Local script variable."))

        # Populate list
        for name, kind, sig, doc in items:
            item = QListWidgetItem(f" {name}  [{kind}]")
            item.setData(Qt.ItemDataRole.UserRole, (name, sig, doc))
            self.list_widget.addItem(item)
            
        if self.list_widget.count() > 0:
            self.list_widget.setCurrentRow(0)
            return True
        return False

    def on_selection_changed(self):
        item = self.list_widget.currentItem()
        if item:
            name, sig, doc = item.data(Qt.ItemDataRole.UserRole)
            self.doc_widget.setHtml(f"<b>{sig}</b><hr style='border:1px solid #1a1a28;'><pre style='white-space:pre-wrap;font-family:inherit;'>{doc}</pre>")
        else:
            self.doc_widget.clear()

    def handle_key(self, event):
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.close()
            return True
        elif key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            row = self.list_widget.currentRow()
            count = self.list_widget.count()
            if key == Qt.Key.Key_Up:
                self.list_widget.setCurrentRow((row - 1) % count if count > 0 else 0)
            else:
                self.list_widget.setCurrentRow((row + 1) % count if count > 0 else 0)
            return True
        elif key in (Qt.Key.Key_Tab, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.complete_text()
            return True
        return False

    def complete_text(self):
        item = self.list_widget.currentItem()
        if item:
            name, _, _ = item.data(Qt.ItemDataRole.UserRole)
            
            # Replace current typed word fragment with target name
            cursor = self.editor.textCursor()
            cursor.select(QTextCursor.SelectionType.WordUnderCursor)
            cursor.insertText(name)
            self.editor.setTextCursor(cursor)
        self.close()

    def show_popup(self):
        cursor_rect = self.editor.cursorRect()
        global_pos = self.editor.viewport().mapToGlobal(cursor_rect.bottomLeft())
        # Add offset
        global_pos += QPoint(0, 5)
        self.move(global_pos)
        self.show()
        self.list_widget.setFocus()
