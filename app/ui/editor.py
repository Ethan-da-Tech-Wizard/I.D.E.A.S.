import os
import re
from PySide6.QtWidgets import QPlainTextEdit, QWidget, QTextEdit, QTabWidget, QTabBar, QSplitter, QApplication
from PySide6.QtGui import QPainter, QColor, QFontMetrics, QPaintEvent, QTextCharFormat, QFont, QDrag
from PySide6.QtCore import Qt, QRect, QSize, Signal, QMimeData, QTimer
from app.ui.highlighter import PythonHighlighter

class LineNumberArea(QWidget):
    def __init__(self, editor):
        super().__init__(editor)
        self.setObjectName("LineNumberArea")
        self.editor = editor

    def sizeHint(self):
        return QSize(self.editor.line_number_area_width(), 0)

    def paintEvent(self, event: QPaintEvent):
        self.editor.lineNumberAreaPaintEvent(event)

class CodeEditor(QPlainTextEdit):
    """Next-generation code editor with line numbers, active line highlights, 
    custom tab widths, and status updates.
    """
    cursor_position_changed = Signal(int, int)
    diagnostics_updated = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.line_number_area = LineNumberArea(self)
        
        self.blockCountChanged.connect(self.update_line_number_area_width)
        self.updateRequest.connect(self.update_line_number_area)
        self.update_line_number_area_width(0)

        # Debounced linter timer
        self.lint_timer = QTimer(self)
        self.lint_timer.setSingleShot(True)
        self.lint_timer.setInterval(300)
        self.lint_timer.timeout.connect(self.run_lint)
        self.textChanged.connect(self.on_text_changed)

        # Autocomplete popup instance
        self.autocomplete_popup = None

        # Set syntax highlighter
        self.highlighter = PythonHighlighter(self.document())

        # Set default monospaced font
        font = QFont()
        font.setFamily("JetBrains Mono")
        font.setPointSize(13)
        font.setStyleHint(QFont.StyleHint.Monospace)
        self.setFont(font)

        # Configure tab stops to be exactly 4 space characters wide
        metrics = QFontMetrics(font)
        self.setTabStopDistance(4 * metrics.horizontalAdvance(' '))

        # Cursor events for status bar & current line highlighting
        self.cursorPositionChanged.connect(self.on_cursor_position_changed)
        self.cursorPositionChanged.connect(self.highlight_current_line)

        # Initial highlighting
        self.highlight_current_line()

        # Git diff status cache
        self.git_diff_status = {}
        self.workspace_path = None
        self.file_path = None

    def line_number_area_width(self):
        digits = 1
        max_val = max(1, self.blockCount())
        while max_val >= 10:
            max_val /= 10
            digits += 1
        space = 15 + self.fontMetrics().horizontalAdvance('9') * digits
        return space

    def update_line_number_area_width(self, _):
        self.setViewportMargins(self.line_number_area_width(), 0, 0, 0)

    def update_line_number_area(self, rect, dy):
        if dy:
            self.line_number_area.scroll(0, dy)
        else:
            self.line_number_area.update(0, rect.y(), self.line_number_area.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self.update_line_number_area_width(0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        cr = self.contentsRect()
        self.line_number_area.setGeometry(QRect(cr.left(), cr.top(), self.line_number_area_width(), cr.height()))

    def lineNumberAreaPaintEvent(self, event):
        painter = QPainter(self.line_number_area)
        painter.fillRect(event.rect(), QColor("#12121a"))
        
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
                painter.drawText(0, top, self.line_number_area.width() - 8, font_metrics.height(),
                                 Qt.AlignmentFlag.AlignRight, number)
                
                # Paint Git Diff indicator strip next to line number
                status = diff_status.get(line_num)
                if status:
                    color = QColor("#00ff00") if status == "added" else (QColor("#ffaa00") if status == "modified" else QColor("#ff0000"))
                    painter.fillRect(self.line_number_area.width() - 4, top, 3, font_metrics.height(), color)
                    
            block = block.next()
            top = bottom
            bottom = top + int(self.blockBoundingRect(block).height())
            block_number += 1

    def highlight_current_line(self):
        extra_selections = []
        if not self.isReadOnly():
            selection = QTextEdit.ExtraSelection()
            line_color = QColor("#6c5ce7")
            line_color.setAlpha(20)  # ~8% opacity tint for subtle line focus
            
            selection.format.setBackground(line_color)
            selection.format.setProperty(QTextCharFormat.Property.FullWidthSelection, True)
            selection.cursor = self.textCursor()
            selection.cursor.clearSelection()
            extra_selections.append(selection)
        self.setExtraSelections(extra_selections)

    def keyPressEvent(self, event):
        # Redirect keyboard events if autocomplete is open
        if self.autocomplete_popup and self.autocomplete_popup.isVisible():
            if event.key() in (Qt.Key.Key_Escape, Qt.Key.Key_Tab, Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Up, Qt.Key.Key_Down):
                if self.autocomplete_popup.handle_key(event):
                    return

        cursor = self.textCursor()
        text = event.text()
        
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
            
        elif text in brackets.values():
            if not cursor.atEnd():
                cursor.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.KeepAnchor, 1)
                if cursor.selectedText() == text:
                    cursor.clearSelection()
                    self.setTextCursor(cursor)
                    event.accept()
                    self.trigger_autocomplete()
                    return
                cursor.setPosition(cursor.position() - 1)
                
        super().keyPressEvent(event)
        self.trigger_autocomplete()

    def trigger_autocomplete(self):
        cursor = self.textCursor()
        cursor.select(QTextCursor.SelectionType.WordUnderCursor)
        fragment = cursor.selectedText()
        
        if fragment and fragment.strip() and (fragment[0].isalpha() or fragment.startswith("_")):
            from app.ui.autocomplete import AutocompletePopup
            if not self.autocomplete_popup:
                self.autocomplete_popup = AutocompletePopup(self)
            if self.autocomplete_popup.update_completions(fragment):
                self.autocomplete_popup.show_popup()
            else:
                self.autocomplete_popup.close()
        else:
            if self.autocomplete_popup:
                self.autocomplete_popup.close()

    def wheelEvent(self, event):
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
                leading_spaces = len(text) - len(text.lstrip(' '))
                num_guides = leading_spaces // 4
                for i in range(1, num_guides + 1):
                    x = self.contentOffset().x() + i * tab_width_px
                    painter.drawLine(x, top, x, bottom)
            block = block.next()
            top = bottom
            bottom = top + int(self.blockBoundingRect(block).height())

    def update_git_diff_cache(self):
        if not getattr(self, 'workspace_path', None) or not getattr(self, 'file_path', None):
            return
        try:
            rel_path = os.path.relpath(self.file_path, self.workspace_path)
            from app.ide.git import git_get_file_diff
            self.git_diff_status = git_get_file_diff(self.workspace_path, rel_path)
            self.line_number_area.update()
        except Exception:
            self.git_diff_status = {}

    def on_cursor_position_changed(self):
        cursor = self.textCursor()
        line = cursor.blockNumber() + 1
        col = cursor.columnNumber() + 1
        self.cursor_position_changed.emit(line, col)

    def on_text_changed(self):
        self.lint_timer.start()
        self.update_git_diff_cache()

    def run_lint(self):
        import ast
        code = self.toPlainText()
        diagnostics = []
        try:
            ast.parse(code)
        except SyntaxError as exc:
            col = exc.offset or 1
            end_col = exc.end_offset or col + 1
            diagnostics.append({
                "lineno": exc.lineno,
                "col": col,
                "end_col": end_col,
                "msg": exc.msg,
                "file": self.file_path or "active_file.py"
            })
        except Exception:
            pass

        self.highlighter.diagnostics = diagnostics
        self.highlighter.rehighlight()
        self.diagnostics_updated.emit(diagnostics)



class EditorTabBar(QTabBar):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.drag_start_pos = None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_start_pos = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if not (event.buttons() & Qt.MouseButton.LeftButton) or self.drag_start_pos is None:
            super().mouseMoveEvent(event)
            return

        if (event.position().toPoint() - self.drag_start_pos).manhattanLength() < QApplication.startDragDistance():
            super().mouseMoveEvent(event)
            return

        if not self.rect().contains(event.position().toPoint()):
            tab_idx = self.tabAt(self.drag_start_pos)
            if tab_idx != -1:
                drag = QDrag(self)
                mime_data = QMimeData()
                mime_data.setText(f"tab_drag:{tab_idx}:{id(self.parentWidget())}")
                drag.setMimeData(mime_data)
                drag.exec(Qt.DropAction.MoveAction)
                self.drag_start_pos = None
        else:
            super().mouseMoveEvent(event)


class EditorTabWidget(QTabWidget):
    _instances = {}

    def __init__(self, parent_area, parent=None):
        super().__init__(parent)
        self.parent_area = parent_area
        self.setTabsClosable(True)
        self.setMovable(True)
        self.setTabBar(EditorTabBar(self))
        self.setAcceptDrops(True)
        self.tabCloseRequested.connect(self.close_tab)
        self.currentChanged.connect(self.on_current_changed)

        EditorTabWidget._instances[str(id(self))] = self

    def close_tab(self, index):
        widget = self.widget(index)
        self.removeTab(index)
        if widget:
            widget.deleteLater()
        if self.count() == 0:
            self.parent_area.remove_tab_widget(self)

    def on_current_changed(self, index):
        if index >= 0:
            self.parent_area.set_active_tab_widget(self)

    def dragEnterEvent(self, event):
        if event.mimeData().hasText() and event.mimeData().text().startswith("tab_drag:"):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasText() and event.mimeData().text().startswith("tab_drag:"):
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
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

                left_boundary = w * 0.25
                right_boundary = w * 0.75
                top_boundary = h * 0.25
                bottom_boundary = h * 0.75

                if pos.x() < left_boundary:
                    self.parent_area.split_tab_widget(self, Qt.Orientation.Horizontal, src_tab_widget, src_idx, before=True)
                elif pos.x() > right_boundary:
                    self.parent_area.split_tab_widget(self, Qt.Orientation.Horizontal, src_tab_widget, src_idx, before=False)
                elif pos.y() < top_boundary:
                    self.parent_area.split_tab_widget(self, Qt.Orientation.Vertical, src_tab_widget, src_idx, before=True)
                elif pos.y() > bottom_boundary:
                    self.parent_area.split_tab_widget(self, Qt.Orientation.Vertical, src_tab_widget, src_idx, before=False)
                else:
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
    currentChanged = Signal(int)

    def __init__(self, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.setObjectName("EditorArea")
        self.active_tab_widget = None

        self.root_tab_widget = EditorTabWidget(self)
        self.addWidget(self.root_tab_widget)
        self.set_active_tab_widget(self.root_tab_widget)

    def set_active_tab_widget(self, tab_widget):
        if self.active_tab_widget != tab_widget:
            if self.active_tab_widget:
                try:
                    self.active_tab_widget.currentChanged.disconnect(self._on_active_tab_changed)
                except RuntimeError:
                    pass
            self.active_tab_widget = tab_widget
            if self.active_tab_widget:
                self.active_tab_widget.currentChanged.connect(self._on_active_tab_changed)
                self._on_active_tab_changed(self.active_tab_widget.currentIndex())

    def _on_active_tab_changed(self, idx):
        self.currentChanged.emit(idx)

    def addTab(self, widget, filename):
        if not self.active_tab_widget:
            self.active_tab_widget = self.root_tab_widget
        idx = self.active_tab_widget.addTab(widget, filename)
        widget.setMinimumSize(400, 100)
        return idx

    def currentWidget(self):
        if self.active_tab_widget:
            return self.active_tab_widget.currentWidget()
        return None

    def currentIndex(self):
        if self.active_tab_widget:
            return self.active_tab_widget.currentIndex()
        return -1

    def setCurrentIndex(self, index):
        if self.active_tab_widget:
            self.active_tab_widget.setCurrentIndex(index)

    def tabText(self, index):
        if self.active_tab_widget:
            return self.active_tab_widget.tabText(index)
        return ""

    def widget(self, index):
        if self.active_tab_widget:
            return self.active_tab_widget.widget(index)
        return None


    def count(self):
        total = 0
        for tab_widget in self.findChildren(EditorTabWidget):
            total += tab_widget.count()
        return total

    def find_tab_by_filename(self, filename):
        for tab_widget in self.findChildren(EditorTabWidget):
            for idx in range(tab_widget.count()):
                if tab_widget.tabText(idx) == filename:
                    return tab_widget, idx
        return None

    def removeTab(self, index):
        if self.active_tab_widget:
            self.active_tab_widget.removeTab(index)

    def setCurrentWidget(self, widget):
        for tab_widget in self.findChildren(EditorTabWidget):
            for idx in range(tab_widget.count()):
                if tab_widget.widget(idx) == widget:
                    tab_widget.setCurrentWidget(widget)
                    self.set_active_tab_widget(tab_widget)
                    return

    def split_tab_widget(self, tab_widget, orientation, src_tab_widget, src_idx, before=True):
        widget = src_tab_widget.widget(src_idx)
        title = src_tab_widget.tabText(src_idx)
        src_tab_widget.removeTab(src_idx)

        new_tab_widget = EditorTabWidget(self)
        new_tab_widget.addTab(widget, title)
        new_tab_widget.setCurrentWidget(widget)

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
            new_splitter.setSizes([300, 300])

        if src_tab_widget.count() == 0:
            self.remove_tab_widget(src_tab_widget)

        self.set_active_tab_widget(new_tab_widget)

    def remove_tab_widget(self, tab_widget):
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

        QTimer.singleShot(0, self.cleanup_splitters)

    def cleanup_splitters(self):
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

