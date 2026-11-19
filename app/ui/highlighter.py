# ==============================================================================
# Program: app/ui/highlighter.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: colours Python source in the code editor (a QSyntaxHighlighter attached to the editor's
# document) and draws red wavy underlines under diagnostic ranges. It is a lightweight regex
# highlighter, not a parser, so it is fast but can mis-colour edge cases.
# Used by: app/ui/editor.py (CodeEditor creates it and assigns `diagnostics` after each syntax check).
# How it works: __init__ builds an ordered list of (regex, format) rules; highlightBlock applies them
# to each line. LATER rules overwrite EARLIER ones where they overlap, so order is deliberate: broad
# rules first (keywords, builtins, operators, numbers, class-like names, calls), then strings and
# comments, then delimiters.
# Known limits / quirks: only triple DOUBLE quotes are tracked across lines (triple single quotes
# are not); strings are single-line; any Capitalised word is coloured as a class name; the
# delimiter rule runs last, so brackets, commas and dots inside strings and comments are repainted
# in the delimiter colour.
# ==============================================================================

from PySide6.QtGui import QSyntaxHighlighter, QTextCharFormat, QColor, QFont
from PySide6.QtCore import Qt, QRegularExpression

class PythonHighlighter(QSyntaxHighlighter):
    """Vibrant cyberpunk syntax highlighter for Python code in the IDE."""
    
    def __init__(self, parent=None):
        """Build all highlighting rules and formats.

        Args:
            parent: the QTextDocument (or QObject) this highlighter attaches to; None creates it detached.
        """
        super().__init__(parent)
        # List of (QRegularExpression, QTextCharFormat) pairs applied in order by highlightBlock.
        self.highlighting_rules = []

        # Word-boundary (\b) patterns so "in" does not colour the inside of "string"; bold neon pink.
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

        # Common built-in functions in cyan; deliberately a short list, not every builtin.
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

        # Italic orange-yellow makes the instance reference easy to spot.
        # 3. 'self' and 'cls' (Vibrant Orange-Yellow)
        self_format = QTextCharFormat()
        self_format.setForeground(QColor("#e5c07b"))
        self_format.setFontItalic(True)
        self.highlighting_rules.append((QRegularExpression(r"\bself\b"), self_format))
        self.highlighting_rules.append((QRegularExpression(r"\bcls\b"), self_format))

        # Matches "@name" and dotted names such as @app.route.
        # 4. Decorators (Lime / Neon Green)
        decorator_format = QTextCharFormat()
        decorator_format.setForeground(QColor("#a6e22e"))
        self.highlighting_rules.append((QRegularExpression(r"@[A-Za-z0-9_.]+"), decorator_format))

        # Each operator is its own regex (escaped for QRegularExpression). Redundant entries (\= and \=\=) are harmless because they colour the same characters.
        # 5. Operators (Bright Purple / Violet)
        operator_format = QTextCharFormat()
        operator_format.setForeground(QColor("#c678dd"))
        operators = [
            r"\+", r"\-", r"\*", r"\/", r"\=", r"\=\=", r"\!\=", r"\<", r"\>",
            r"\%\=", r"\+\=", r"\-\=", r"\*\=", r"\/=", r"\&", r"\|", r"\^"
        ]
        for op in operators:
            self.highlighting_rules.append((QRegularExpression(op), operator_format))

        # Integers/decimals and 0x hex literals in peach.
        # 6. Numbers (Vibrant Peach / Orange)
        number_format = QTextCharFormat()
        number_format.setForeground(QColor("#d19a66"))
        self.highlighting_rules.append((QRegularExpression(r"\b[0-9]+(\.[0-9]+)?\b"), number_format))
        self.highlighting_rules.append((QRegularExpression(r"\b0x[0-9a-fA-F]+\b"), number_format))

        # Heuristic: any word starting with a capital letter is treated as a class name.
        # 7. Class Names (Neon Yellow/Gold)
        class_format = QTextCharFormat()
        class_format.setForeground(QColor("#e5c07b"))
        class_format.setFontWeight(QFont.Weight.Bold)
        self.highlighting_rules.append((QRegularExpression(r"\b[A-Z][a-zA-Z0-9_]*\b"), class_format))

        # Lookahead (?=\() colours names followed by "(" without including the parenthesis.
        # 8. Functions (Bright Blue)
        function_format = QTextCharFormat()
        function_format.setForeground(QColor("#61afef"))
        self.highlighting_rules.append((QRegularExpression(r"\b[A-Za-z0-9_]+(?=\()"), function_format))

        # Single-line "..." and '...' with backslash escapes; defined after the keyword rules so a keyword inside a string is repainted as string.
        # 9. Strings (Soft Greenish-Yellow)
        string_format = QTextCharFormat()
        string_format.setForeground(QColor("#98c379"))
        self.highlighting_rules.append((QRegularExpression(r'"[^"\\]*(?:\\.[^"\\]*)*"'), string_format))
        self.highlighting_rules.append((QRegularExpression(r"'[^'\\]*(?:\\.[^'\\]*)*'"), string_format))

        # "#" to the end of the line; muted italic. Because it comes after the string rule, a "#" inside a string would also start a comment colour (known limitation).
        # 10. Comments (Muted Slate / Grey)
        comment_format = QTextCharFormat()
        comment_format.setForeground(QColor("#5c6370"))
        comment_format.setFontItalic(True)
        self.highlighting_rules.append((QRegularExpression(r"#[^\n]*"), comment_format))

        # Brackets, colon, comma and dot in teal.
        # 11. Delimiters (Neon Cyan/Blue/Grey)
        delimiter_format = QTextCharFormat()
        delimiter_format.setForeground(QColor("#56b6c2"))
        delimiters = [r"\(", r"\)", r"\[", r"\]", r"\{", r"\}", r"\:", r"\,", r"\."]
        for delim in delimiters:
            self.highlighting_rules.append((QRegularExpression(delim), delimiter_format))

        # Only the format is created here; highlightBlock applies it using block state.
        # 12. Multi-line docstring format
        self.docstring_format = QTextCharFormat()
        self.docstring_format.setForeground(QColor("#4b5263"))
        self.docstring_format.setFontItalic(True)
        # List of dicts {"lineno", "col", "end_col", ...} set by the editor; each gets a wavy red underline.
        self.diagnostics = []

    def highlightBlock(self, text):
        """Qt calls this for each text block (line) that needs colouring.

        Args:
            text: the block's text.
        """
        # globalMatch yields every match of a regex in the line; setFormat paints its character range.
        # 1. Apply standard highlighting rules
        for pattern, format in self.highlighting_rules:
            iterator = pattern.globalMatch(text)
            while iterator.hasNext():
                match = iterator.next()
                self.setFormat(match.capturedStart(), match.capturedLength(), format)

        # Block state 1 means "this line ends inside an unterminated triple-quoted string", carried from the previous line by Qt via previousBlockState().
        # 2. Multi-line Docstrings (Triple Single or Double Quotes)
        # Reset; set to 1 below only if a docstring is left open.
        self.setCurrentBlockState(0)
        
        start_expression = QRegularExpression(r'"""')
        end_expression = QRegularExpression(r'"""')
        
        start_index = 0
        # If the previous line left a docstring open, the whole start of this line is still inside it (start_index stays 0); otherwise look for an opening quote.
        if self.previousBlockState() != 1:
            match = start_expression.match(text)
            start_index = match.capturedStart()
        
        # Loop handles several docstrings on one line: colour from an opening to its closing quote, then search again.
        while start_index >= 0:
            match = end_expression.match(text, start_index + 3)
            end_index = match.capturedStart()
            
            # No closing quotes on this line: colour to the end and mark the block as open for the next line.
            if end_index == -1:
                self.setCurrentBlockState(1)
                comment_length = len(text) - start_index
            else:
                comment_length = end_index - start_index + 3
                
            self.setFormat(start_index, comment_length, self.docstring_format)
            
            if end_index == -1:
                break
                
            start_index = start_expression.match(text, start_index + comment_length).capturedStart()

        # Diagnostics use 1-based line and column numbers; blockNumber() is 0-based, hence the +1.
        # 3. Apply wavy underlines for diagnostics
        diags = getattr(self, 'diagnostics', [])
        for diag in diags:
            if self.currentBlock().blockNumber() + 1 == diag.get("lineno"):
                # Convert to a 0-based start column; end defaults to two characters if unknown, length is at least 1 so something is always underlined.
                start = max(0, diag.get("col", 1) - 1)
                end = diag.get("end_col", start + 2) - 1
                length = max(1, end - start)
                for pos in range(start, start + length):
                    if pos < len(text):
                        # Copy the existing colour at each position and add the underline, so syntax colours are preserved under the squiggle.
                        fmt = self.format(pos)
                        fmt.setUnderlineStyle(QTextCharFormat.UnderlineStyle.WaveUnderline)
                        fmt.setUnderlineColor(QColor("#ff3333"))
                        self.setFormat(pos, 1, fmt)
