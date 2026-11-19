# ==============================================================================
# Program: app/ui/terminal.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: a small interactive Python console widget for the IDE's bottom panel. The user types an
# expression or statement after the ">>> " prompt, presses Enter, and sees the printed output. It
# is an INTENTIONAL exception to the project's "no dynamic code execution" rule: scripts/security_audit.py
# lists app/ui/terminal.py as allowed for compile, eval and exec (see ALLOWED_CALLS there).
# Used by: app/application.py (bottom-panel tab). If a workspace path is given it is exposed to
# typed code as the variable WORKSPACE.
# Behaviour: Up/Down browse history; the cursor and Backspace cannot move before the prompt;
# expressions print repr(result); statements run silently; exceptions print "Name: message".
# Security notes (honest): the "restriction to workspace" is only the WORKSPACE variable. Typed code
# runs in the app process with the user's full privileges; sys.stdout/sys.stderr are swapped
# temporarily (not thread-safe); there is no timeout, so `while True: pass` freezes the UI.
# ==============================================================================

import sys
from io import StringIO
from PySide6.QtWidgets import QPlainTextEdit
from PySide6.QtGui import QTextCursor
from PySide6.QtCore import Qt

class TerminalWidget(QPlainTextEdit):
    """An interactive Python shell widget restricted to workspace execution context."""
    
    def __init__(self, workspace_path=None, parent=None):
        """Create the console with an empty history and show the first prompt.

        Args:
            workspace_path: optional folder exposed to typed code as WORKSPACE.
            parent: optional Qt parent widget.
        """
        super().__init__(parent)
        self.workspace_path = workspace_path
        self.setObjectName("TerminalWidget")
        
        # history holds past commands; history_idx == -1 means "not browsing"; current_input keeps the line being typed while browsing so Down can restore it.
        # Command history
        self.history = []
        self.history_idx = -1
        self.current_input = ""
        
        # objectName "TerminalWidget" scopes the stylesheet; green-on-black monospace mimics a terminal.
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
        
        # Fresh globals dict shared by all commands in this console, so variables persist between lines (like a REPL).
        # Execution namespace
        self.locals = {
            "__name__": "__console__",
            "__doc__": None,
        }
        if self.workspace_path:
            self.locals["WORKSPACE"] = self.workspace_path
            
        # prompt_pos (set below) marks where editable input starts; everything before it is read-only history.
        self.prompt = ">>> "
        self.insertPlainText(self.prompt)
        self.set_prompt_position()

    def set_prompt_position(self):
        """Remember the text position just after the prompt; text before it must not be edited."""
        self.prompt_pos = self.textCursor().position()

    def get_current_line(self):
        """Return the text the user has typed after the prompt (up to the end of the document)."""
        cursor = self.textCursor()
        # Anchor at the prompt, then extend the selection to the end: the selection is exactly the typed input.
        cursor.setPosition(self.prompt_pos)
        cursor.movePosition(QTextCursor.MoveOperation.End, QTextCursor.MoveMode.KeepAnchor)
        return cursor.selectedText()

    def replace_current_line(self, text):
        """Replace the typed input with `text` (used for history navigation).

        Args:
            text: new input text.
        """
        cursor = self.textCursor()
        cursor.setPosition(self.prompt_pos)
        cursor.movePosition(QTextCursor.MoveOperation.End, QTextCursor.MoveMode.KeepAnchor)
        cursor.insertText(text)

    def keyPressEvent(self, event):
        """Intercept keys to keep the prompt intact, run commands on Enter and browse history.

        Args:
            event: the Qt key event; accepted (and not passed on) for keys handled here.
        """
        cursor = self.textCursor()
        
        # Mouse clicks can put the cursor in old output; snap it back to the input area before typing.
        # Prevent cursor from moving before the prompt position
        if cursor.position() < self.prompt_pos:
            cursor.setPosition(self.prompt_pos)
            self.setTextCursor(cursor)

        # Enter: take the typed line, move to a new line, record it and run it. A blank line just shows a new prompt.
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            cmd = self.get_current_line()
            self.insertPlainText("\n")
            if cmd.strip():
                self.history.append(cmd)
                # Running a command ends any history browsing.
                self.history_idx = -1
                self.execute_command(cmd)
            else:
                self.insertPlainText(self.prompt)
                self.set_prompt_position()
            self.ensureCursorVisible()
            event.accept()
            return

        # Block Backspace/Left at the prompt boundary so the prompt cannot be erased.
        elif event.key() == Qt.Key.Key_Backspace:
            if cursor.position() <= self.prompt_pos:
                event.accept()
                return

        elif event.key() == Qt.Key.Key_Left:
            if cursor.position() <= self.prompt_pos:
                event.accept()
                return

        # Up: step to older commands, saving the half-typed line on the first press.
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

        # Down: step to newer commands; past the newest, restore the saved half-typed line.
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

        # All other keys (letters, paste, Right...) behave like a normal text editor.
        super().keyPressEvent(event)

    def execute_command(self, cmd):
        """Run one command in the console namespace and append its output and a new prompt.

        Args:
            cmd: the line typed by the user.

        Side effects: temporarily replaces sys.stdout and sys.stderr; may mutate self.locals; runs
        arbitrary Python (see the file header's security notes).
        """
        # Capture print() output into a buffer so it can be shown in the widget instead of the real console.
        # Redirect stdout and stderr
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        redirected = StringIO()
        sys.stdout = redirected
        sys.stderr = redirected

        # Outer try reports any exception from the typed code; the finally block always restores the real streams.
        try:
            try:
                # Try "eval" mode first: a single expression yields a value to echo, like a normal Python REPL.
                # Try to evaluate expression
                code = compile(cmd, "<console>", "eval")
                res = eval(code, self.locals)
                if res is not None:
                    print(repr(res))
            # Not an expression (for example an assignment or a for loop): compile again as statements.
            except SyntaxError:
                # Try to execute statement
                code = compile(cmd, "<console>", "exec")
                exec(code, self.locals)
        except Exception as exc:
            # Written to the redirected stderr, so it appears in the widget.
            print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr

        # Everything printed during the command; may be empty (statements often print nothing).
        output = redirected.getvalue()
        if output:
            self.insertPlainText(output)
        self.insertPlainText(self.prompt)
        self.set_prompt_position()
        self.ensureCursorVisible()
