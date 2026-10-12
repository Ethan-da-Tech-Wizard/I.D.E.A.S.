import sys
from io import StringIO
from PySide6.QtWidgets import QPlainTextEdit
from PySide6.QtGui import QTextCursor
from PySide6.QtCore import Qt

class TerminalWidget(QPlainTextEdit):
    """An interactive Python shell widget restricted to workspace execution context."""
    
    def __init__(self, workspace_path=None, parent=None):
        super().__init__(parent)
        self.workspace_path = workspace_path
        self.setObjectName("TerminalWidget")
        
        # Command history
        self.history = []
        self.history_idx = -1
        self.current_input = ""
        
        # Styling that matches OutputConsole QSS
        self.setStyleSheet("""
            QPlainTextEdit#TerminalWidget {
                background-color: #050508;
                border: 1px solid #1a1a28;
                border-radius: 6px;
                color: #a0e0b0;
                font-family: 'JetBrains Mono', 'Fira Code', 'Consolas', monospace;
                font-size: 12px;
                padding: 10px;
            }
        """)
        
        # Execution namespace
        self.locals = {
            "__name__": "__console__",
            "__doc__": None,
        }
        if self.workspace_path:
            self.locals["WORKSPACE"] = self.workspace_path
            
        self.prompt = ">>> "
        self.insertPlainText(self.prompt)
        self.set_prompt_position()

    def set_prompt_position(self):
        self.prompt_pos = self.textCursor().position()

    def get_current_line(self):
        cursor = self.textCursor()
        cursor.setPosition(self.prompt_pos)
        cursor.movePosition(QTextCursor.MoveOperation.End, QTextCursor.MoveMode.KeepAnchor)
        return cursor.selectedText()

    def replace_current_line(self, text):
        cursor = self.textCursor()
        cursor.setPosition(self.prompt_pos)
        cursor.movePosition(QTextCursor.MoveOperation.End, QTextCursor.MoveMode.KeepAnchor)
        cursor.insertText(text)

    def keyPressEvent(self, event):
        cursor = self.textCursor()
        
        # Prevent cursor from moving before the prompt position
        if cursor.position() < self.prompt_pos:
            cursor.setPosition(self.prompt_pos)
            self.setTextCursor(cursor)

        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            cmd = self.get_current_line()
            self.insertPlainText("\n")
            if cmd.strip():
                self.history.append(cmd)
                self.history_idx = -1
                self.execute_command(cmd)
            else:
                self.insertPlainText(self.prompt)
                self.set_prompt_position()
            self.ensureCursorVisible()
            event.accept()
            return

        elif event.key() == Qt.Key.Key_Backspace:
            if cursor.position() <= self.prompt_pos:
                event.accept()
                return

        elif event.key() == Qt.Key.Key_Left:
            if cursor.position() <= self.prompt_pos:
                event.accept()
                return

        elif event.key() == Qt.Key.Key_Up:
            if self.history:
                if self.history_idx == -1:
                    self.current_input = self.get_current_line()
                    self.history_idx = len(self.history) - 1
                elif self.history_idx > 0:
                    self.history_idx -= 1
                self.replace_current_line(self.history[self.history_idx])
            event.accept()
            return

        elif event.key() == Qt.Key.Key_Down:
            if self.history and self.history_idx != -1:
                if self.history_idx < len(self.history) - 1:
                    self.history_idx += 1
                    self.replace_current_line(self.history[self.history_idx])
                else:
                    self.history_idx = -1
                    self.replace_current_line(self.current_input)
            event.accept()
            return

        super().keyPressEvent(event)

    def execute_command(self, cmd):
        # Redirect stdout and stderr
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        redirected = StringIO()
        sys.stdout = redirected
        sys.stderr = redirected

        try:
            try:
                # Try to evaluate expression
                code = compile(cmd, "<console>", "eval")
                res = eval(code, self.locals)
                if res is not None:
                    print(repr(res))
            except SyntaxError:
                # Try to execute statement
                code = compile(cmd, "<console>", "exec")
                exec(code, self.locals)
        except Exception as exc:
            print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr

        output = redirected.getvalue()
        if output:
            self.insertPlainText(output)
        self.insertPlainText(self.prompt)
        self.set_prompt_position()
        self.ensureCursorVisible()
