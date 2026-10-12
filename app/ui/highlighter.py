from PySide6.QtGui import QSyntaxHighlighter, QTextCharFormat, QColor, QFont
from PySide6.QtCore import Qt, QRegularExpression

class PythonHighlighter(QSyntaxHighlighter):
    """Vibrant cyberpunk syntax highlighter for Python code in the IDE."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.highlighting_rules = []

        # 1. Keywords (Neon Magenta/Pink)
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

        # 2. Built-ins (Neon Cyan)
        builtin_format = QTextCharFormat()
        builtin_format.setForeground(QColor("#00f0ff"))
        builtins = [
            "print", "len", "range", "str", "int", "float", "list", "dict",
            "set", "tuple", "bool", "open", "super", "object", "type", "dir",
            "help", "enumerate", "zip", "map", "filter", "any", "all", "sum"
        ]
        for word in builtins:
            pattern = QRegularExpression(rf"\b{word}\b")
            self.highlighting_rules.append((pattern, builtin_format))

        # 3. 'self' and 'cls' (Vibrant Orange-Yellow)
        self_format = QTextCharFormat()
        self_format.setForeground(QColor("#e5c07b"))
        self_format.setFontItalic(True)
        self.highlighting_rules.append((QRegularExpression(r"\bself\b"), self_format))
        self.highlighting_rules.append((QRegularExpression(r"\bcls\b"), self_format))

        # 4. Decorators (Lime / Neon Green)
        decorator_format = QTextCharFormat()
        decorator_format.setForeground(QColor("#a6e22e"))
        self.highlighting_rules.append((QRegularExpression(r"@[A-Za-z0-9_.]+"), decorator_format))

        # 5. Operators (Bright Purple / Violet)
        operator_format = QTextCharFormat()
        operator_format.setForeground(QColor("#c678dd"))
        operators = [
            r"\+", r"\-", r"\*", r"\/", r"\=", r"\=\=", r"\!\=", r"\<", r"\>",
            r"\%\=", r"\+\=", r"\-\=", r"\*\=", r"\/=", r"\&", r"\|", r"\^"
        ]
        for op in operators:
            self.highlighting_rules.append((QRegularExpression(op), operator_format))

        # 6. Numbers (Vibrant Peach / Orange)
        number_format = QTextCharFormat()
        number_format.setForeground(QColor("#d19a66"))
        self.highlighting_rules.append((QRegularExpression(r"\b[0-9]+(\.[0-9]+)?\b"), number_format))
        self.highlighting_rules.append((QRegularExpression(r"\b0x[0-9a-fA-F]+\b"), number_format))

        # 7. Class Names (Neon Yellow/Gold)
        class_format = QTextCharFormat()
        class_format.setForeground(QColor("#e5c07b"))
        class_format.setFontWeight(QFont.Weight.Bold)
        self.highlighting_rules.append((QRegularExpression(r"\b[A-Z][a-zA-Z0-9_]*\b"), class_format))

        # 8. Functions (Bright Blue)
        function_format = QTextCharFormat()
        function_format.setForeground(QColor("#61afef"))
        self.highlighting_rules.append((QRegularExpression(r"\b[A-Za-z0-9_]+(?=\()"), function_format))

        # 9. Strings (Soft Greenish-Yellow)
        string_format = QTextCharFormat()
        string_format.setForeground(QColor("#98c379"))
        self.highlighting_rules.append((QRegularExpression(r'"[^"\\]*(?:\\.[^"\\]*)*"'), string_format))
        self.highlighting_rules.append((QRegularExpression(r"'[^'\\]*(?:\\.[^'\\]*)*'"), string_format))

        # 10. Comments (Muted Slate / Grey)
        comment_format = QTextCharFormat()
        comment_format.setForeground(QColor("#5c6370"))
        comment_format.setFontItalic(True)
        self.highlighting_rules.append((QRegularExpression(r"#[^\n]*"), comment_format))

        # 11. Delimiters (Neon Cyan/Blue/Grey)
        delimiter_format = QTextCharFormat()
        delimiter_format.setForeground(QColor("#56b6c2"))
        delimiters = [r"\(", r"\)", r"\[", r"\]", r"\{", r"\}", r"\:", r"\,", r"\."]
        for delim in delimiters:
            self.highlighting_rules.append((QRegularExpression(delim), delimiter_format))

        # 12. Multi-line docstring format
        self.docstring_format = QTextCharFormat()
        self.docstring_format.setForeground(QColor("#4b5263"))
        self.docstring_format.setFontItalic(True)
        self.diagnostics = []

    def highlightBlock(self, text):
        # 1. Apply standard highlighting rules
        for pattern, format in self.highlighting_rules:
            iterator = pattern.globalMatch(text)
            while iterator.hasNext():
                match = iterator.next()
                self.setFormat(match.capturedStart(), match.capturedLength(), format)

        # 2. Multi-line Docstrings (Triple Single or Double Quotes)
        self.setCurrentBlockState(0)
        
        start_expression = QRegularExpression(r'"""')
        end_expression = QRegularExpression(r'"""')
        
        start_index = 0
        if self.previousBlockState() != 1:
            match = start_expression.match(text)
            start_index = match.capturedStart()
        
        while start_index >= 0:
            match = end_expression.match(text, start_index + 3)
            end_index = match.capturedStart()
            
            if end_index == -1:
                self.setCurrentBlockState(1)
                comment_length = len(text) - start_index
            else:
                comment_length = end_index - start_index + 3
                
            self.setFormat(start_index, comment_length, self.docstring_format)
            
            if end_index == -1:
                break
                
            start_index = start_expression.match(text, start_index + comment_length).capturedStart()

        # 3. Apply wavy underlines for diagnostics
        diags = getattr(self, 'diagnostics', [])
        for diag in diags:
            if self.currentBlock().blockNumber() + 1 == diag.get("lineno"):
                start = max(0, diag.get("col", 1) - 1)
                end = diag.get("end_col", start + 2) - 1
                length = max(1, end - start)
                for pos in range(start, start + length):
                    if pos < len(text):
                        fmt = self.format(pos)
                        fmt.setUnderlineStyle(QTextCharFormat.UnderlineStyle.WaveUnderline)
                        fmt.setUnderlineColor(QColor("#ff3333"))
                        self.setFormat(pos, 1, fmt)
