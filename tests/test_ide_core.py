# ==============================================================================
# Program: tests/test_ide_core.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: smoke/behaviour tests for four IDE pieces that need little setup: macro parameter parsing
# (MacroPanel), keybinding presets, the problems table and editor autocompletion. The Qt widgets
# need a QApplication, supplied by a module-scoped fixture.
# Run: python3 -m pytest tests/test_ide_core.py -q   (set QT_QPA_PLATFORM=offscreen on headless hosts)
# ==============================================================================

import os
import pytest
from app.ui.macro_panel import MacroPanel
from app.ide.keybindings import KeybindingsEngine, PRESETS
from app.ui.problems import ProblemsWidget
from app.ui.autocomplete import AutocompletePopup, BUILTINS_DOC
from PySide6.QtWidgets import QApplication

# Module scope: Qt allows one QApplication per process, so it is created once and shared.
@pytest.fixture(scope="module")

def qapp():
    """Return the existing QApplication or create one (widgets cannot be built without it)."""
    # Provide a QApplication instance for widgets that require it
    # Reuse an instance another test module may already have created; a second one would raise.
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app

# Scenario: "# @param name = default [min, max]" comment lines become widget descriptions. The four
# lines cover each type the parser must infer from the default: int, float, bool and a quoted
# string with an options list. Order must follow the source order.
def test_macro_parsing(qapp):
    # Test that comments with parameters are parsed correctly
    panel = MacroPanel()
    # @param syntax: name = default [range or options]. The range/options part is optional (bypass has none).
    code = """
# @param cutoff = 150 [20, 2000]
# @param volume = 0.8 [0.0, 1.0]
# @param bypass = False
# @param wave = "sine" [sine, square, saw]
    """
    params = panel.parse_macros(code)
    
    assert len(params) == 4
    
    # Ints stay ints (150, not 150.0) and carry their min/max for the slider.
    # Check cutoff
    assert params[0]["name"] == "cutoff"
    assert params[0]["default"] == 150
    assert params[0]["kind"] == "int"
    assert params[0]["min"] == 20
    assert params[0]["max"] == 2000
    
    # A decimal default makes it a float; the range is float too.
    # Check volume
    assert params[1]["name"] == "volume"
    assert params[1]["default"] == 0.8
    assert params[1]["kind"] == "float"
    assert params[1]["min"] == 0.0
    assert params[1]["max"] == 1.0
    
    # Booleans have no range, only a default.
    # Check bypass
    assert params[2]["name"] == "bypass"
    assert params[2]["default"] is False
    assert params[2]["kind"] == "bool"
    
    # Quotes are stripped from string defaults; bracketed names become the dropdown options.
    # Check wave
    assert params[3]["name"] == "wave"
    assert params[3]["default"] == "sine"
    assert params[3]["kind"] == "str"
    assert params[3]["options"] == ["sine", "square", "saw"]

# Pins the preset table the user can switch between: names exist, and a few anchor bindings
# (Save, sidebar toggle, Emacs chord with a comma separator) have the documented key strings.
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

# Scenario: two diagnostics are loaded. Row count and column layout are asserted: column 1 =
# message, 2 = file, 3 = line number as text, so the panel's columns cannot be reordered silently.
def test_problems_widget(qapp):
    # Test problems table populates correctly
    widget = ProblemsWidget()
    problems = [
        {"lineno": 5, "col": 12, "end_col": 15, "msg": "Invalid syntax", "file": "test.py"},
        {"lineno": 10, "col": 1, "end_col": 3, "msg": "Unexpected indent", "file": "main.py"}
    ]
    
    # update_problems replaces the table contents with the new list.
    widget.update_problems(problems)
    
    assert widget.rowCount() == 2
    assert widget.item(0, 1).text() == "Invalid syntax"
    assert widget.item(0, 2).text() == "test.py"
    assert widget.item(1, 3).text() == "10"

# Scenario: an editor holding a class, a method and a local variable. The popup must offer built-in
# API names (e.g. play_sample) for a prefix, and names found in the current document (CustomSynth)
# for another prefix, proving the document is indexed as well as the static docs.
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
    # Matching is by prefix against built-ins.
    popup.update_completions("play")
    items = [popup.list_widget.item(i).text().strip() for i in range(popup.list_widget.count())]
    assert any("play_sample" in item for item in items)
    
    # Test local completions
    # Matching against names harvested from the editor text.
    popup.update_completions("Cust")
    items = [popup.list_widget.item(i).text().strip() for i in range(popup.list_widget.count())]
    assert any("CustomSynth" in item for item in items)
