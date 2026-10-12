import os
import json
from PySide6.QtWidgets import QMessageBox
from PySide6.QtGui import QKeySequence
from app.security import read_text_file_safely

PRESETS = {
    "VS Code": {
        "File:New": "Ctrl+N",
        "File:Open": "Ctrl+O",
        "File:Save": "Ctrl+S",
        "Layout:ToggleSidebar": "Ctrl+B",
        "Layout:ToggleConsole": "Ctrl+`",
        "Editor:RunCode": "Ctrl+R",
        "Editor:StopCode": "Ctrl+K"
    },
    "Sublime": {
        "File:New": "Ctrl+N",
        "File:Open": "Ctrl+O",
        "File:Save": "Ctrl+S",
        "Layout:ToggleSidebar": "Ctrl+B",
        "Layout:ToggleConsole": "Ctrl+`",
        "Editor:RunCode": "Ctrl+B",
        "Editor:StopCode": "Ctrl+Shift+K"
    },
    "Emacs": {
        "File:New": "Ctrl+X,Ctrl+N",
        "File:Open": "Ctrl+X,Ctrl+F",
        "File:Save": "Ctrl+X,Ctrl+S",
        "Layout:ToggleSidebar": "Ctrl+X,Ctrl+B",
        "Layout:ToggleConsole": "Ctrl+X,Ctrl+`",
        "Editor:RunCode": "Ctrl+C,Ctrl+C",
        "Editor:StopCode": "Ctrl+C,Ctrl+K"
    }
}

class KeybindingsEngine:
    def __init__(self, main_window):
        self.main_window = main_window
        # Find keybindings.json in project root
        self.keybindings_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "keybindings.json"
        )

    def load_and_apply(self):
        # 1. Start with VS Code defaults
        bindings = PRESETS["VS Code"].copy()
        
        # 2. Try loading from user's custom JSON
        preset_name = "VS Code"
        if os.path.exists(self.keybindings_path):
            try:
                content = read_text_file_safely(self.keybindings_path)
                data = json.loads(content)
                preset_name = data.get("preset", "VS Code")
                if preset_name in PRESETS:
                    bindings.update(PRESETS[preset_name])
                # User custom overrides
                user_bindings = data.get("bindings", {})
                bindings.update(user_bindings)
            except Exception as e:
                print(f"Error loading keybindings.json: {e}")

        # 3. Collision detection
        reversed_bindings = {}
        collisions = []
        for action, key in bindings.items():
            if not key:
                continue
            norm_key = QKeySequence(key).toString()
            if norm_key in reversed_bindings:
                collisions.append((norm_key, reversed_bindings[norm_key], action))
            else:
                reversed_bindings[norm_key] = action

        if collisions:
            msg = "Keybinding collisions detected!\n\n"
            for key, act1, act2 in collisions:
                msg += f"  '{key}' is bound to both '{act1}' and '{act2}'\n"
            msg += "\nThe last binding will override the conflict."
            print(msg)
            # Show warning dialog
            QMessageBox.warning(self.main_window, "Keybinding Conflicts", msg)

        # 4. Map actions on main window
        self.apply_binding(self.main_window.new_file_act, bindings.get("File:New"))
        self.apply_binding(self.main_window.open_file_act, bindings.get("File:Open"))
        self.apply_binding(self.main_window.save_project_act, bindings.get("File:Save"))
        self.apply_binding(self.main_window.sidebar_toggle_act, bindings.get("Layout:ToggleSidebar"))
        self.apply_binding(self.main_window.console_toggle_act, bindings.get("Layout:ToggleConsole"))
        self.apply_binding(self.main_window.run_code_act, bindings.get("Editor:RunCode"))
        self.apply_binding(self.main_window.stop_code_act, bindings.get("Editor:StopCode"))

    def apply_binding(self, action, key_str):
        if action and key_str:
            action.setShortcut(QKeySequence(key_str))
