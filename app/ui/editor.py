# ==============================================================================
# Program: app/ui/editor.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: the code editor of the IDE mode. CodeEditor is a QPlainTextEdit with line numbers, git
# change markers, current-line highlight, indentation guides, bracket auto-closing, autocomplete,
# Ctrl+wheel zoom and a debounced Python syntax check. EditorArea/EditorTabWidget/EditorTabBar
# give VS Code style tabs that can be dragged to split the editor into several panes.
# Classes: LineNumberArea (gutter widget painted by the editor), CodeEditor, EditorTabBar (starts
# tab drags), EditorTabWidget (one pane of tabs, accepts drops), EditorArea (a QSplitter holding
# panes; exposes the small QTabWidget-like API the main window expects).
# Used by: app/application.py (creates CodeEditor per file, connects cursor_position_changed and
# diagnostics_updated, adds them to the EditorArea); highlighter.py (colours and squiggles);
# autocomplete.py (popup).
# Known bugs noticed (comments-only pass; (1) has since been fixed): (1) FIXED: QTextCursor is used in
# keyPressEvent and trigger_autocomplete and used to be missing from the PySide6.QtGui import, so typing a
# bracket/quote or any key that reached trigger_autocomplete raised NameError inside the Qt event
# handler; (2) update_git_diff_cache runs a git command on every text change (no debounce); (3) in
# paintEvent the second setPen call replaces the guide colour (#2a2a3e) with the default pen colour
# and drawLine receives float coordinates; (4) the tab drag payload uses id() of a Python object,
# which is only valid inside this process; (5) run_lint parses every file as Python, whatever its
# extension; (6) the `re` import is unused.
# ==============================================================================

import os
import re
from PySide6.QtWidgets import QPlainTextEdit, QWidget, QTextEdit, QTabWidget, QTabBar, QSplitter, QApplication
from PySide6.QtGui import QPainter, QColor, QFontMetrics, QPaintEvent, QTextCharFormat, QFont, QDrag, QTextCursor
from PySide6.QtCore import Qt, QRect, QSize, Signal, QMimeData, QTimer
from app.ui.highlighter import PythonHighlighter

class LineNumberArea(QWidget):
    """Narrow widget at the editor's left edge that shows line numbers. It paints nothing itself; it asks the editor (lineNumberAreaPaintEvent) so all layout maths stays in one place."""
    def __init__(self, editor):
        """Args:
            editor: the CodeEditor that owns and sizes this gutter.
        """
        super().__init__(editor)
        # Name used by the stylesheet to colour the gutter.
        self.setObjectName("LineNumberArea")
        self.editor = editor

    def sizeHint(self):
        """Qt asks for the preferred width; height is irrelevant because the editor sets the geometry."""
        return QSize(self.editor.line_number_area_width(), 0)

    def paintEvent(self, event: QPaintEvent):
        """Delegate painting to the editor."""
        self.editor.lineNumberAreaPaintEvent(event)

class CodeEditor(QPlainTextEdit):
    """Next-generation code editor with line numbers, active line highlights, 
    custom tab widths, and status updates.
    """
    # cursor_position_changed(line, column) feeds the status bar (1-based); diagnostics_updated(list) carries the syntax errors found by run_lint to the Problems panel.
    cursor_position_changed = Signal(int, int)
    diagnostics_updated = Signal(list)

    def __init__(self, parent=None):
        """Create the editor, wire gutter updates, debounced lint, highlighter, font and tab stops.

        Args:
            parent: optional Qt parent.
        """
        super().__init__(parent)
        self.line_number_area = LineNumberArea(self)
        
        # The gutter must widen when the line count gains a digit (99 -> 100) and scroll/repaint with the text.
        self.blockCountChanged.connect(self.update_line_number_area_width)
        self.updateRequest.connect(self.update_line_number_area)
        self.update_line_number_area_width(0)

        # Single-shot 300 ms timer restarted on every edit: the syntax check runs only after the user pauses typing.
        # Debounced linter timer
        self.lint_timer = QTimer(self)
        self.lint_timer.setSingleShot(True)
        self.lint_timer.setInterval(300)
        self.lint_timer.timeout.connect(self.run_lint)
        self.textChanged.connect(self.on_text_changed)

        # Autocomplete popup instance
        # Created lazily on first use (see trigger_autocomplete).
        self.autocomplete_popup = None

        # Attached to this editor's document; it recolours automatically as text changes.
        # Set syntax highlighter
        self.highlighter = PythonHighlighter(self.document())

        # JetBrains Mono if installed; the Monospace style hint makes Qt pick another fixed-width font otherwise.
        # Set default monospaced font
        font = QFont()
        font.setFamily("JetBrains Mono")
        font.setPointSize(13)
        font.setStyleHint(QFont.StyleHint.Monospace)
        self.setFont(font)

        # A tab character renders as exactly four spaces wide.
        # Configure tab stops to be exactly 4 space characters wide
        metrics = QFontMetrics(font)
        self.setTabStopDistance(4 * metrics.horizontalAdvance(' '))

        # Every caret move updates the status bar and moves the current-line highlight.
        # Cursor events for status bar & current line highlighting
        self.cursorPositionChanged.connect(self.on_cursor_position_changed)
        self.cursorPositionChanged.connect(self.highlight_current_line)

        # Initial highlighting
        self.highlight_current_line()

        # {line number: "added" | "modified" | "deleted"}; filled by update_git_diff_cache, drawn as coloured bars in the gutter. workspace_path and file_path are set by the main window after construction.
        # Git diff status cache
        self.git_diff_status = {}
        self.workspace_path = None
        self.file_path = None

    def line_number_area_width(self):
        """Return the gutter width in pixels: 15 px padding plus one digit width per digit of the largest line number."""
        digits = 1
        max_val = max(1, self.blockCount())
        # Counts decimal digits (dividing by 10 until a single digit remains).
        while max_val >= 10:
            max_val /= 10
            digits += 1
        space = 15 + self.fontMetrics().horizontalAdvance('9') * digits
        return space

    def update_line_number_area_width(self, _):
        """Reserve room for the gutter by moving the text viewport's left margin (slot for blockCountChanged; the argument is unused)."""
        self.setViewportMargins(self.line_number_area_width(), 0, 0, 0)

    def update_line_number_area(self, rect, dy):
        """Slot for updateRequest: scroll or repaint the gutter in step with the text area.

        Args:
            rect: region of the viewport that needs repainting.
            dy: vertical scroll distance (non-zero when scrolled).
        """
        # Scrolled: move the existing gutter pixels instead of repainting everything.
        if dy:
            self.line_number_area.scroll(0, dy)
        else:
            self.line_number_area.update(0, rect.y(), self.line_number_area.width(), rect.height())
        # The whole viewport changed (for example a font or size change): recompute the gutter width as well.
        if rect.contains(self.viewport().rect()):
            self.update_line_number_area_width(0)

    def resizeEvent(self, event):
        """Keep the gutter glued to the left edge and as tall as the editor."""
        super().resizeEvent(event)
        cr = self.contentsRect()
        self.line_number_area.setGeometry(QRect(cr.left(), cr.top(), self.line_number_area_width(), cr.height()))

    def lineNumberAreaPaintEvent(self, event):
        """Paint the visible line numbers and git change bars into the gutter.

        Args:
            event: paint event for the gutter widget; only its rect is repainted.
        """
        painter = QPainter(self.line_number_area)
        painter.fillRect(event.rect(), QColor("#12121a"))
        
        # Walk only the blocks (lines) that are visible; `top`/`bottom` are each line's pixel range in gutter coordinates.
        block = self.firstVisibleBlock()
        block_number = block.blockNumber()
        top = int(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + int(self.blockBoundingRect(block).height())

        painter.setPen(QColor("#4b5263"))
        font_metrics = self.fontMetrics()
        diff_status = getattr(self, 'git_diff_status', {})

        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                line_num = block_number + 1
                number = str(line_num)
                # Right-aligned number with an 8 px right margin.
                painter.drawText(0, top, self.line_number_area.width() - 8, font_metrics.height(),
                                 Qt.AlignmentFlag.AlignRight, number)
                
                # 3 px bar at the right edge of the gutter: green = added, orange = modified, red = deleted.
                # Paint Git Diff indicator strip next to line number
                status = diff_status.get(line_num)
                if status:
                    color = QColor("#00ff00") if status == "added" else (QColor("#ffaa00") if status == "modified" else QColor("#ff0000"))
                    painter.fillRect(self.line_number_area.width() - 4, top, 3, font_metrics.height(), color)
                    
            # Advance: the next line starts where this one ended.
            block = block.next()
            top = bottom
            bottom = top + int(self.blockBoundingRect(block).height())
            block_number += 1

    def highlight_current_line(self):
        """Tint the line containing the caret with a faint purple full-width band."""
        extra_selections = []
        # Read-only editors (previews) get no highlight.
        if not self.isReadOnly():
            selection = QTextEdit.ExtraSelection()
            line_color = QColor("#6c5ce7")
            # Alpha 20 of 255, about 8 percent, so the band is subtle on the dark theme.
            line_color.setAlpha(20)  # ~8% opacity tint for subtle line focus
            
            selection.format.setBackground(line_color)
            selection.format.setProperty(QTextCharFormat.Property.FullWidthSelection, True)
            selection.cursor = self.textCursor()
            selection.cursor.clearSelection()
            extra_selections.append(selection)
        self.setExtraSelections(extra_selections)

    def keyPressEvent(self, event):
        """Handle autocomplete navigation and bracket/quote auto-closing, then normal typing.

        Args:
            event: the key event; accepted and not passed on when handled here.
        """
        # While the popup is open, Esc/Tab/Enter/Up/Down belong to it (accept a completion, move selection, close).
        # Redirect keyboard events if autocomplete is open
        if self.autocomplete_popup and self.autocomplete_popup.isVisible():
            if event.key() in (Qt.Key.Key_Escape, Qt.Key.Key_Tab, Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Up, Qt.Key.Key_Down):
                if self.autocomplete_popup.handle_key(event):
                    return

        cursor = self.textCursor()
        text = event.text()
        
        # Typing an opener inserts the pair and puts the caret between them; with a selection the selection is wrapped instead. (Uses QTextCursor, see Known bugs.)
        # Smart delimiter auto-close
        brackets = {'(': ')', '[': ']', '{': '}', '"': '"', "'": "'"}
        if text in brackets:
            closing = brackets[text]
            if cursor.hasSelection():
                sel = cursor.selectedText()
                cursor.insertText(text + sel + closing)
            else:
                cursor.insertText(text + closing)
                cursor.movePosition(QTextCursor.MoveOperation.Left, QTextCursor.MoveMode.MoveAnchor, 1)
                self.setTextCursor(cursor)
            event.accept()
            self.trigger_autocomplete()
            return
            
        # Typing a closer when the very next character is the same closer just steps over it instead of inserting a duplicate.
        elif text in brackets.values():
            if not cursor.atEnd():
                cursor.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.KeepAnchor, 1)
                if cursor.selectedText() == text:
                    cursor.clearSelection()
                    self.setTextCursor(cursor)
                    event.accept()
                    self.trigger_autocomplete()
                    return
                # Not a skip-over case: undo the one-character selection so normal insertion proceeds.
                cursor.setPosition(cursor.position() - 1)
                
        # Default text editing for every other key.
        super().keyPressEvent(event)
        self.trigger_autocomplete()

    def trigger_autocomplete(self):
        """Show or hide the completion popup for the word under the caret.

        A word qualifies when it starts with a letter or underscore. The popup class is imported here, lazily, to avoid a circular import with autocomplete.py.
        """
        cursor = self.textCursor()
        cursor.select(QTextCursor.SelectionType.WordUnderCursor)
        fragment = cursor.selectedText()
        
        if fragment and fragment.strip() and (fragment[0].isalpha() or fragment.startswith("_")):
            from app.ui.autocomplete import AutocompletePopup
            if not self.autocomplete_popup:
                self.autocomplete_popup = AutocompletePopup(self)
            # update_completions returns True when there is something to show.
            if self.autocomplete_popup.update_completions(fragment):
                self.autocomplete_popup.show_popup()
            else:
                self.autocomplete_popup.close()
        else:
            if self.autocomplete_popup:
                self.autocomplete_popup.close()

    def wheelEvent(self, event):
        """Ctrl+wheel zooms the font in or out; plain wheel scrolls as usual."""
        if event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            if delta > 0:
                self.zoomIn(1)
            elif delta < 0:
                self.zoomOut(1)
            event.accept()
        else:
            super().wheelEvent(event)

    def paintEvent(self, event):
        """Draw the text, then dotted vertical indentation guides over it.

        Guides are drawn every 4 leading spaces (the tab width), so only space-indented code gets guides.
        """
        # The base class paints text first; guides are an overlay.
        super().paintEvent(event)
        
        # Draw dotted indentation guides
        painter = QPainter(self.viewport())
        painter.setPen(QColor("#2a2a3e"))
        painter.setPen(Qt.PenStyle.DotLine)
        
        metrics = self.fontMetrics()
        char_width = metrics.horizontalAdvance(' ')
        tab_width_px = 4 * char_width
        
        block = self.firstVisibleBlock()
        top = int(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + int(self.blockBoundingRect(block).height())
        
        while block.isValid() and top <= self.viewport().rect().bottom():
            if block.isVisible() and bottom >= self.viewport().rect().top():
                text = block.text()
                # Count of leading spaces; integer division by 4 gives the number of indent levels to mark.
                leading_spaces = len(text) - len(text.lstrip(' '))
                num_guides = leading_spaces // 4
                for i in range(1, num_guides + 1):
                    x = self.contentOffset().x() + i * tab_width_px
                    painter.drawLine(x, top, x, bottom)
            block = block.next()
            top = bottom
            bottom = top + int(self.blockBoundingRect(block).height())

    def update_git_diff_cache(self):
        """Refresh git change markers for the open file.

        Silently clears the markers when there is no workspace/file or git reports anything wrong, since a missing repository is normal.
        """
        if not getattr(self, 'workspace_path', None) or not getattr(self, 'file_path', None):
            return
        try:
            # Git expects a path relative to the repository (workspace) root.
            rel_path = os.path.relpath(self.file_path, self.workspace_path)
            # Imported inside the method so the editor module loads without the git wrapper being needed.
            from app.ide.git import git_get_file_diff
            self.git_diff_status = git_get_file_diff(self.workspace_path, rel_path)
            self.line_number_area.update()
        except Exception:
            self.git_diff_status = {}

    def on_cursor_position_changed(self):
        """Emit the 1-based (line, column) for the status bar."""
        cursor = self.textCursor()
        line = cursor.blockNumber() + 1
        col = cursor.columnNumber() + 1
        self.cursor_position_changed.emit(line, col)

    def on_text_changed(self):
        """Restart the lint timer and refresh git markers after each edit."""
        self.lint_timer.start()
        self.update_git_diff_cache()

    def run_lint(self):
        """Parse the text with ast and report a syntax error (if any) to the highlighter and the Problems panel.

        Emits diagnostics_updated with a list of dicts {lineno, col, end_col, msg, file}; the list is empty when the code parses.
        """
        # Local import: only needed when linting actually runs.
        import ast
        code = self.toPlainText()
        diagnostics = []
        try:
            # Parsing does not execute the code; it only checks syntax.
            ast.parse(code)
        except SyntaxError as exc:
            # SyntaxError offsets may be None; default to column 1 and a two-character span.
            col = exc.offset or 1
            end_col = exc.end_offset or col + 1
            diagnostics.append({
                "lineno": exc.lineno,
                "col": col,
                "end_col": end_col,
                "msg": exc.msg,
                "file": self.file_path or "active_file.py"
            })
        # Other parse failures (for example null bytes) are ignored rather than shown.
        except Exception:
            pass

        self.highlighter.diagnostics = diagnostics
        # Force a repaint so squiggles appear/disappear immediately.
        self.highlighter.rehighlight()
        self.diagnostics_updated.emit(diagnostics)



class EditorTabBar(QTabBar):
    """Tab bar that starts a drag when a tab is pulled out of the bar, so the tab can be dropped on another pane's edge to split the view or on its centre to move it."""
    def __init__(self, parent=None):
        """Create the tab bar with no drag in progress."""
        super().__init__(parent)
        # Where the left button went down; None when no drag is possible.
        self.drag_start_pos = None

    def mousePressEvent(self, event):
        """Remember the press position (for drag detection) then let Qt select the tab."""
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_start_pos = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        """Start a drag once the mouse leaves the tab bar after moving farther than the platform drag threshold."""
        if not (event.buttons() & Qt.MouseButton.LeftButton) or self.drag_start_pos is None:
            super().mouseMoveEvent(event)
            return

        # Ignore tiny movements so ordinary clicks do not start drags.
        if (event.position().toPoint() - self.drag_start_pos).manhattanLength() < QApplication.startDragDistance():
            super().mouseMoveEvent(event)
            return

        # Still inside the bar: let Qt reorder tabs normally (the tab widget is movable). Leaving the bar starts a drag.
        if not self.rect().contains(event.position().toPoint()):
            tab_idx = self.tabAt(self.drag_start_pos)
            if tab_idx != -1:
                drag = QDrag(self)
                mime_data = QMimeData()
                # Payload "tab_drag:<tab index>:<id of the source tab widget>"; EditorTabWidget.dropEvent parses it and looks the widget up in _instances.
                mime_data.setText(f"tab_drag:{tab_idx}:{id(self.parentWidget())}")
                drag.setMimeData(mime_data)
                drag.exec(Qt.DropAction.MoveAction)
                self.drag_start_pos = None
        else:
            super().mouseMoveEvent(event)


class EditorTabWidget(QTabWidget):
    # Registry of all live tab widgets keyed by str(id(widget)), so a drop can find the drag's source widget from the payload.
    _instances = {}

    def __init__(self, parent_area, parent=None):
        """Create one pane of tabs.

        Args:
            parent_area: the EditorArea that manages splitting and which pane is active.
            parent: optional Qt parent.
        """
        super().__init__(parent)
        self.parent_area = parent_area
        self.setTabsClosable(True)
        # Tabs can be reordered inside the bar.
        self.setMovable(True)
        self.setTabBar(EditorTabBar(self))
        self.setAcceptDrops(True)
        self.tabCloseRequested.connect(self.close_tab)
        self.currentChanged.connect(self.on_current_changed)

        EditorTabWidget._instances[str(id(self))] = self

    def close_tab(self, index):
        """Remove a tab and its editor widget; an emptied pane asks the area to remove it."""
        widget = self.widget(index)
        self.removeTab(index)
        if widget:
            # Schedule deletion so pending events for the editor finish first.
            widget.deleteLater()
        if self.count() == 0:
            self.parent_area.remove_tab_widget(self)

    def on_current_changed(self, index):
        """Make this pane the area's active pane when its current tab changes."""
        if index >= 0:
            self.parent_area.set_active_tab_widget(self)

    def dragEnterEvent(self, event):
        """Accept only drags carrying our tab payload."""
        if event.mimeData().hasText() and event.mimeData().text().startswith("tab_drag:"):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        """Keep accepting tab drags while the pointer moves over the pane."""
        if event.mimeData().hasText() and event.mimeData().text().startswith("tab_drag:"):
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        """Decide what a dropped tab does based on where it landed.

        Drop zones: the outer quarters (left, right, top, bottom) split this pane in that direction; the middle moves the tab into this pane.
        """
        text = event.mimeData().text()
        parts = text.split(":")
        if len(parts) == 3 and parts[0] == "tab_drag":
            src_idx = int(parts[1])
            src_id = parts[2]
            src_tab_widget = EditorTabWidget._instances.get(src_id)
            if src_tab_widget:
                pos = event.position().toPoint()
                w = self.width()
                h = self.height()

                # Zone edges at 25 percent and 75 percent of the width and height.
                left_boundary = w * 0.25
                right_boundary = w * 0.75
                top_boundary = h * 0.25
                bottom_boundary = h * 0.75

                # Horizontal zones are checked first, so corners count as left/right.
                if pos.x() < left_boundary:
                    self.parent_area.split_tab_widget(self, Qt.Orientation.Horizontal, src_tab_widget, src_idx, before=True)
                elif pos.x() > right_boundary:
                    self.parent_area.split_tab_widget(self, Qt.Orientation.Horizontal, src_tab_widget, src_idx, before=False)
                elif pos.y() < top_boundary:
                    self.parent_area.split_tab_widget(self, Qt.Orientation.Vertical, src_tab_widget, src_idx, before=True)
                elif pos.y() > bottom_boundary:
                    self.parent_area.split_tab_widget(self, Qt.Orientation.Vertical, src_tab_widget, src_idx, before=False)
                else:
                    # Dropping a tab onto the centre of its own pane does nothing.
                    if src_tab_widget != self:
                        widget = src_tab_widget.widget(src_idx)
                        title = src_tab_widget.tabText(src_idx)
                        src_tab_widget.removeTab(src_idx)
                        self.addTab(widget, title)
                        self.setCurrentWidget(widget)
                        if src_tab_widget.count() == 0:
                            self.parent_area.remove_tab_widget(src_tab_widget)
                event.acceptProposedAction()


class EditorArea(QSplitter):
    """Splitter holding one or more EditorTabWidget panes; provides the QTabWidget-style methods (addTab, currentWidget, count...) that act on the ACTIVE pane so the rest of the app can ignore splitting."""
    # Re-emitted when the current tab of the active pane changes (same name as QTabWidget's signal on purpose).
    currentChanged = Signal(int)

    def __init__(self, parent=None):
        """Start with a single pane."""
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.setObjectName("EditorArea")
        self.active_tab_widget = None

        self.root_tab_widget = EditorTabWidget(self)
        self.addWidget(self.root_tab_widget)
        self.set_active_tab_widget(self.root_tab_widget)

    def set_active_tab_widget(self, tab_widget):
        """Switch which pane receives new tabs and drives currentChanged; moves the signal connection from the old pane to the new one."""
        if self.active_tab_widget != tab_widget:
            if self.active_tab_widget:
                try:
                    self.active_tab_widget.currentChanged.disconnect(self._on_active_tab_changed)
                # The old pane may already be deleted; disconnecting then raises, which is harmless.
                except RuntimeError:
                    pass
            self.active_tab_widget = tab_widget
            if self.active_tab_widget:
                self.active_tab_widget.currentChanged.connect(self._on_active_tab_changed)
                self._on_active_tab_changed(self.active_tab_widget.currentIndex())

    def _on_active_tab_changed(self, idx):
        """Forward the active pane's currentChanged as this area's own signal."""
        self.currentChanged.emit(idx)

    def addTab(self, widget, filename):
        """Add `widget` to the active pane under `filename` and return its index within that pane."""
        if not self.active_tab_widget:
            self.active_tab_widget = self.root_tab_widget
        idx = self.active_tab_widget.addTab(widget, filename)
        # Keeps split panes from being squeezed to unusable widths.
        widget.setMinimumSize(400, 100)
        return idx

    def currentWidget(self):
        """Return the current editor of the active pane (or None)."""
        if self.active_tab_widget:
            return self.active_tab_widget.currentWidget()
        return None

    def currentIndex(self):
        """Return the current tab index within the active pane (-1 if none)."""
        if self.active_tab_widget:
            return self.active_tab_widget.currentIndex()
        return -1

    def setCurrentIndex(self, index):
        """Select a tab by index in the active pane."""
        if self.active_tab_widget:
            self.active_tab_widget.setCurrentIndex(index)

    def tabText(self, index):
        """Return the title of a tab in the active pane."""
        if self.active_tab_widget:
            return self.active_tab_widget.tabText(index)
        return ""

    def widget(self, index):
        """Return the widget at a tab index of the active pane."""
        if self.active_tab_widget:
            return self.active_tab_widget.widget(index)
        return None


    def count(self):
        """Return the total number of tabs across ALL panes."""
        total = 0
        for tab_widget in self.findChildren(EditorTabWidget):
            total += tab_widget.count()
        return total

    def find_tab_by_filename(self, filename):
        """Find an already open file by tab title; returns (pane, index) or None. The title is the file name, so two files with the same name in different folders are indistinguishable."""
        for tab_widget in self.findChildren(EditorTabWidget):
            for idx in range(tab_widget.count()):
                if tab_widget.tabText(idx) == filename:
                    return tab_widget, idx
        return None

    def removeTab(self, index):
        """Remove a tab by index from the active pane."""
        if self.active_tab_widget:
            self.active_tab_widget.removeTab(index)

    def setCurrentWidget(self, widget):
        """Find the pane containing `widget`, select it there and make that pane active."""
        for tab_widget in self.findChildren(EditorTabWidget):
            for idx in range(tab_widget.count()):
                if tab_widget.widget(idx) == widget:
                    tab_widget.setCurrentWidget(widget)
                    self.set_active_tab_widget(tab_widget)
                    return

    def split_tab_widget(self, tab_widget, orientation, src_tab_widget, src_idx, before=True):
        """Move a dragged tab into a NEW pane next to `tab_widget`.

        Args:
            tab_widget: the pane that received the drop.
            orientation: Qt.Horizontal (side by side) or Qt.Vertical (stacked).
            src_tab_widget, src_idx: source pane and index of the tab being moved.
            before: put the new pane before (left/top) or after (right/bottom).
        """
        widget = src_tab_widget.widget(src_idx)
        title = src_tab_widget.tabText(src_idx)
        src_tab_widget.removeTab(src_idx)

        new_tab_widget = EditorTabWidget(self)
        new_tab_widget.addTab(widget, title)
        new_tab_widget.setCurrentWidget(widget)

        # Reuse the parent splitter when it already has the wanted orientation; otherwise wrap the pane and the new pane in a nested splitter.
        parent_splitter = tab_widget.parentWidget()
        if not isinstance(parent_splitter, QSplitter):
            parent_splitter = self

        if parent_splitter.orientation() == orientation:
            idx = parent_splitter.indexOf(tab_widget)
            insert_idx = idx if before else idx + 1
            parent_splitter.insertWidget(insert_idx, new_tab_widget)
        else:
            idx = parent_splitter.indexOf(tab_widget)
            new_splitter = QSplitter(orientation)
            new_splitter.setMinimumWidth(400)
            parent_splitter.insertWidget(idx, new_splitter)
            if before:
                new_splitter.addWidget(new_tab_widget)
                new_splitter.addWidget(tab_widget)
            else:
                new_splitter.addWidget(tab_widget)
                new_splitter.addWidget(new_tab_widget)
            # Equal initial sizes (a 50/50 split).
            new_splitter.setSizes([300, 300])

        if src_tab_widget.count() == 0:
            self.remove_tab_widget(src_tab_widget)

        self.set_active_tab_widget(new_tab_widget)

    def remove_tab_widget(self, tab_widget):
        """Remove an emptied pane, but never the last remaining one; pick another pane as active and tidy up splitters."""
        all_tabs = self.findChildren(EditorTabWidget)
        if len(all_tabs) <= 1:
            return

        tab_widget.hide()
        tab_widget.deleteLater()
        if str(id(tab_widget)) in EditorTabWidget._instances:
            del EditorTabWidget._instances[str(id(tab_widget))]

        if self.active_tab_widget == tab_widget:
            remaining_tabs = [t for t in self.findChildren(EditorTabWidget) if t != tab_widget and t.isVisible()]
            if remaining_tabs:
                self.set_active_tab_widget(remaining_tabs[0])

        # Deferred to the next event-loop turn, after Qt has finished the removal.
        QTimer.singleShot(0, self.cleanup_splitters)

    def cleanup_splitters(self):
        """Collapse splitters that became empty or hold a single child, so the layout does not keep useless nesting."""
        # Depth-first: clean children before judging the splitter itself. A splitter with one child is replaced by that child in its parent.
        def _clean(widget):
            if not isinstance(widget, QSplitter):
                return

            for i in range(widget.count()):
                _clean(widget.widget(i))

            if widget != self:
                if widget.count() == 0:
                    widget.hide()
                    widget.deleteLater()
                elif widget.count() == 1:
                    child = widget.widget(0)
                    parent = widget.parentWidget()
                    if isinstance(parent, QSplitter):
                        idx = parent.indexOf(widget)
                        child.setParent(parent)
                        parent.insertWidget(idx, child)
                        widget.hide()
                        widget.deleteLater()
        _clean(self)

