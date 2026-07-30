from PySide6.QtGui import QSyntaxHighlighter, QTextCharFormat, QColor, QFont
from PySide6.QtCore import Qt, QRegularExpression

class PythonHighlighter(QSyntaxHighlighter):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.highlighting_rules = []

        # Keywords (Neon Magenta)
        keyword_format = QTextCharFormat()
        keyword_format.setForeground(QColor("#ff007f"))
        keyword_format.setFontWeight(QFont.Weight.Bold)
        keywords = [
            "and", "as", "assert", "break", "class", "continue", "def",
            "del", "elif", "else", "except", "False", "finally", "for",
            "from", "global", "if", "import", "in", "is", "lambda", "None",
            "nonlocal", "not", "or", "pass", "raise", "return", "True",
            "try", "while", "with", "yield"
        ]
        for word in keywords:
            pattern = QRegularExpression(rf"\b{word}\b")
            self.highlighting_rules.append((pattern, keyword_format))

        # Built-ins (Neon Cyan)
        builtin_format = QTextCharFormat()
        builtin_format.setForeground(QColor("#00f0ff"))
        builtins = ["print", "len", "range", "str", "int", "float", "list", "dict", "set"]
        for word in builtins:
            pattern = QRegularExpression(rf"\b{word}\b")
            self.highlighting_rules.append((pattern, builtin_format))

        # Strings (Yellow / Greenish)
        string_format = QTextCharFormat()
        string_format.setForeground(QColor("#e5c07b"))
        self.highlighting_rules.append((QRegularExpression("\".*\""), string_format))
        self.highlighting_rules.append((QRegularExpression("'.*'"), string_format))

        # Functions (Blueish)
        function_format = QTextCharFormat()
        function_format.setForeground(QColor("#61afef"))
        self.highlighting_rules.append((QRegularExpression(r"\b[A-Za-z0-9_]+(?=\()"), function_format))

        # Comments (Grey)
        comment_format = QTextCharFormat()
        comment_format.setForeground(QColor("#5c6370"))
        comment_format.setFontItalic(True)
        self.highlighting_rules.append((QRegularExpression("#[^\n]*"), comment_format))

    def highlightBlock(self, text):
        for pattern, format in self.highlighting_rules:
            iterator = pattern.globalMatch(text)
            while iterator.hasNext():
                match = iterator.next()
                self.setFormat(match.capturedStart(), match.capturedLength(), format)
