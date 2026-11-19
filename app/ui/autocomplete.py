# ==============================================================================
# Program: autocomplete.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: Provides contextual code autocompletion popups, language symbol suggestions, keyword matching, and snippet expansion for the source code editor.
# ==============================================================================
# How this module fits: CodeEditor (app/ui/editor.py) creates one AutocompletePopup per editor. When the user types identifier characters the editor calls
# update_completions(prefix); if it returns True the editor calls show_popup() and forwards Up/Down/Tab/Enter/Escape to handle_key() while the popup is open.
# Choosing a suggestion replaces the word being typed. Suggestions come from two sources only: the built-in functions of the app's "safe script" language
# (BUILTINS_DOC, matching what app/security.py run_safe_script allows) and names found in the current file by simple text patterns.
# Inputs: the editor's text and the typed prefix. Outputs: a popup list with a small documentation pane, and text inserted into the editor.
# Design decisions: regular expressions instead of a real parser keep it instant and dependency-free; the popup is a top-level Qt "Popup" window so it can overlap other widgets and
# closes itself on outside clicks; styling is a private style sheet so it looks the same whatever theme is active.
# Known limits noticed (comment pass only):
#   - Symbols come from text patterns, so "def x" inside a string or comment, and keyword arguments such as f(a=1), are also offered; imports and attributes (obj.method) are not.
#   - Matching is case-sensitive prefix matching only; there is no fuzzy search or ranking beyond "built-ins, functions, classes, variables".
#   - The documentation pane inserts the doc text into HTML without escaping; today the text is constant or built from identifier-only names, so this is safe, but any future doc string with "<" would render as markup.
#   - Qt.WindowFlags.Popup is used for the window flag; in PySide6 the documented enum is Qt.WindowType.Popup, so this may need updating for the installed PySide6 version (cannot be checked here because PySide6 is not installed).
#   - The built-in docs mention "NeonDAW", the project's earlier name.

import re
from PySide6.QtWidgets import QFrame, QListWidget, QListWidgetItem, QHBoxLayout, QTextEdit
from PySide6.QtCore import Qt, QRect, QPoint
from PySide6.QtGui import QTextCursor

# Documentation shown for the built-in functions of the safe script language. Key = function name offered in the list; "sig" = signature line shown in bold;
# "doc" = description with an example, displayed in the right-hand pane. Only these two names exist because run_safe_script supports only play_sample and print.
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
    """Small popup window with a suggestion list on the left and a read-only documentation pane on the right."""
    def __init__(self, editor):
        """Build the popup UI.

        Args:
            editor: the CodeEditor (QPlainTextEdit) this popup belongs to; used as the Qt parent, as the text source and for cursor position.
        """
        # Qt.Popup windows close automatically when the user clicks elsewhere and are drawn above the editor; the editor is the parent so the popup is destroyed with it.
        super().__init__(editor, Qt.WindowFlags.Popup)
        self.editor = editor
        # Object names are selectors for the private style sheet below.
        self.setObjectName("AutocompletePopup")
        
        # Horizontal layout: list (stretch 1) and documentation pane (stretch 1, fixed 240 px wide so the list gets the remaining 240 px of the 480 px window).
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        self.list_widget = QListWidget(self)
        self.list_widget.setObjectName("AutocompleteList")
        # Selecting a row (arrow keys or click) refreshes the documentation; double-click completes immediately.
        self.list_widget.itemSelectionChanged.connect(self.on_selection_changed)
        self.list_widget.itemDoubleClicked.connect(self.complete_text)
        layout.addWidget(self.list_widget, 1)
        
        self.doc_widget = QTextEdit(self)
        self.doc_widget.setObjectName("AutocompleteDoc")
        self.doc_widget.setReadOnly(True)
        self.doc_widget.setFixedWidth(240)
        layout.addWidget(self.doc_widget, 1)
        
        # Fixed size (480 x 160 px) so the popup never jumps around while the list changes length; the list scrolls instead.
        self.setFixedSize(480, 160)
        
        # Cyberpunk style: dark background (#12121a), violet border/selection (#6c5ce7) to match the application's accent colours; monospace 11 px so names line up.
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
        """Rebuild the suggestion list for the typed text.

        Args:
            prefix: the characters typed so far (may be empty, which lists everything).

        Returns:
            True if at least one suggestion exists (the first is pre-selected); False otherwise, so the caller can skip showing the popup.
        """
        self.list_widget.clear()
        
        # Built-ins first so they appear at the top of the list.
        # Collect built-ins
        items = []
        for name, info in BUILTINS_DOC.items():
            if name.startswith(prefix):
                items.append((name, "builtin", info["sig"], info["doc"]))
                
        # Scan the whole file each time with regular expressions: cheap for lesson-sized scripts, and avoids parsing code that is often incomplete while being typed.
        # Scan current file symbols
        code = self.editor.toPlainText()
        functions = re.findall(r"def\s+([A-Za-z_][A-Za-z0-9_]*)", code)
        classes = re.findall(r"class\s+([A-Za-z_][A-Za-z0-9_]*)", code)
        # Variables: a name followed by "=" but not "==" (the [^=] excludes comparisons); this also matches keyword arguments and the left side of augmented assignments.
        variables = re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*=[^=]", code)
        
        # Sorted unique names so the list order is stable between keystrokes; each group skips names already offered by an earlier group.
        for name in sorted(list(set(functions))):
            if name.startswith(prefix) and name not in BUILTINS_DOC:
                items.append((name, "function", f"def {name}()", f"Custom function defined in this script."))
                
        for name in sorted(list(set(classes))):
            if name.startswith(prefix):
                items.append((name, "class", f"class {name}", f"Custom class defined in this script."))
                
        for name in sorted(list(set(variables))):
            if name.startswith(prefix) and name not in BUILTINS_DOC and name not in functions and name not in classes:
                items.append((name, "variable", name, f"Local script variable."))

        # Each row stores (name, signature, documentation) in UserRole; the display text shows the kind in brackets.
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
        """Show the documentation of the highlighted suggestion, or clear the pane when nothing is selected."""
        item = self.list_widget.currentItem()
        if item:
            name, sig, doc = item.data(Qt.ItemDataRole.UserRole)
            # The signature and doc are placed in HTML; <pre> with pre-wrap keeps the example's line breaks (see the escaping note in the file header).
            self.doc_widget.setHtml(f"<b>{sig}</b><hr style='border:1px solid #1a1a28;'><pre style='white-space:pre-wrap;font-family:inherit;'>{doc}</pre>")
        else:
            self.doc_widget.clear()

    def handle_key(self, event):
        """Handle a key press forwarded by the editor while the popup is open.

        Args:
            event: the QKeyEvent.

        Returns:
            True if the popup consumed the key (so the editor must not process it), False to let the editor handle it (for example ordinary typing).
        """
        key = event.key()
        # Escape cancels without inserting anything.
        if key == Qt.Key.Key_Escape:
            self.close()
            return True
        elif key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            # Up/Down wrap around at the ends (modulo the row count); with no rows the selection stays on row 0.
            row = self.list_widget.currentRow()
            count = self.list_widget.count()
            if key == Qt.Key.Key_Up:
                self.list_widget.setCurrentRow((row - 1) % count if count > 0 else 0)
            else:
                self.list_widget.setCurrentRow((row + 1) % count if count > 0 else 0)
            return True
        # Tab, Return and Enter all accept the highlighted suggestion, as in most code editors.
        elif key in (Qt.Key.Key_Tab, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.complete_text()
            return True
        return False

    def complete_text(self):
        """Insert the highlighted suggestion and close the popup (also the double-click handler).

        Does nothing but close if no row is selected.
        """
        item = self.list_widget.currentItem()
        if item:
            name, _, _ = item.data(Qt.ItemDataRole.UserRole)
            
            # Replace current typed word fragment with target name: WordUnderCursor selects the identifier around the cursor, so the half-typed prefix is overwritten instead of duplicated.
            # Replace current typed word fragment with target name
            cursor = self.editor.textCursor()
            cursor.select(QTextCursor.SelectionType.WordUnderCursor)
            cursor.insertText(name)
            self.editor.setTextCursor(cursor)
        self.close()

    def show_popup(self):
        """Show the popup just below the text cursor and give it keyboard focus."""
        # cursorRect is in the editor viewport's coordinates, so convert to screen coordinates before moving the top-level popup window.
        cursor_rect = self.editor.cursorRect()
        global_pos = self.editor.viewport().mapToGlobal(cursor_rect.bottomLeft())
        # Add offset: 5 px down so the popup does not cover the line being typed
        global_pos += QPoint(0, 5)
        self.move(global_pos)
        self.show()
        # Focus the list so arrow keys work; the editor still receives keys through handle_key (see the editor's keyPressEvent).
        self.list_widget.setFocus()
