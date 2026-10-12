import os
import pytest
from app.ui.macro_panel import MacroPanel
from app.ide.keybindings import KeybindingsEngine, PRESETS
from app.ui.problems import ProblemsWidget
from app.ui.autocomplete import AutocompletePopup, BUILTINS_DOC
from PySide6.QtWidgets import QApplication

@pytest.fixture(scope="module")
def qapp():
    # Provide a QApplication instance for widgets that require it
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app

def test_macro_parsing(qapp):
    # Test that comments with parameters are parsed correctly
    panel = MacroPanel()
    code = """
# @param cutoff = 150 [20, 2000]
# @param volume = 0.8 [0.0, 1.0]
# @param bypass = False
# @param wave = "sine" [sine, square, saw]
    """
    params = panel.parse_macros(code)
    
    assert len(params) == 4
    
    # Check cutoff
    assert params[0]["name"] == "cutoff"
    assert params[0]["default"] == 150
    assert params[0]["kind"] == "int"
    assert params[0]["min"] == 20
    assert params[0]["max"] == 2000
    
    # Check volume
    assert params[1]["name"] == "volume"
    assert params[1]["default"] == 0.8
    assert params[1]["kind"] == "float"
    assert params[1]["min"] == 0.0
    assert params[1]["max"] == 1.0
    
    # Check bypass
    assert params[2]["name"] == "bypass"
    assert params[2]["default"] is False
    assert params[2]["kind"] == "bool"
    
    # Check wave
    assert params[3]["name"] == "wave"
    assert params[3]["default"] == "sine"
    assert params[3]["kind"] == "str"
    assert params[3]["options"] == ["sine", "square", "saw"]

def test_keybindings_presets():
    # Verify the existence and structure of presets
    assert "VS Code" in PRESETS
    assert "Sublime" in PRESETS
    assert "Emacs" in PRESETS
    
    vs_code = PRESETS["VS Code"]
    assert vs_code["File:Save"] == "Ctrl+S"
    assert vs_code["Layout:ToggleSidebar"] == "Ctrl+B"
    
    emacs = PRESETS["Emacs"]
    assert emacs["File:Save"] == "Ctrl+X,Ctrl+S"

def test_problems_widget(qapp):
    # Test problems table populates correctly
    widget = ProblemsWidget()
    problems = [
        {"lineno": 5, "col": 12, "end_col": 15, "msg": "Invalid syntax", "file": "test.py"},
        {"lineno": 10, "col": 1, "end_col": 3, "msg": "Unexpected indent", "file": "main.py"}
    ]
    
    widget.update_problems(problems)
    
    assert widget.rowCount() == 2
    assert widget.item(0, 1).text() == "Invalid syntax"
    assert widget.item(0, 2).text() == "test.py"
    assert widget.item(1, 3).text() == "10"

def test_autocomplete_indexing(qapp):
    from app.ui.editor import CodeEditor
    editor = CodeEditor()
    editor.setPlainText("""
class CustomSynth:
    def trigger_adsr(self):
        play_rate = 1.0
    """)
    popup = AutocompletePopup(editor)
    
    # Test builtin completions
    popup.update_completions("play")
    items = [popup.list_widget.item(i).text().strip() for i in range(popup.list_widget.count())]
    assert any("play_sample" in item for item in items)
    
    # Test local completions
    popup.update_completions("Cust")
    items = [popup.list_widget.item(i).text().strip() for i in range(popup.list_widget.count())]
    assert any("CustomSynth" in item for item in items)
