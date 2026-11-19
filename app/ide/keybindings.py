# ==============================================================================
# Program: keybindings.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: Defines customizable keyboard shortcut tables, action bindings, chord sequence dispatchers, and input mapping profiles across IDE editing and playback modes.
# ==============================================================================
# How this module fits: app/application.py creates one KeybindingsEngine after it has built the QActions (new_file_act, open_file_act, ...)
# and calls load_and_apply() once. This module never creates actions itself; it only decides which key sequence each existing action
# gets, so renaming an action attribute on the main window silently breaks the matching shortcut (AttributeError at startup).
# Inputs: the PRESETS table below plus an optional keybindings.json in the project root: {"preset": "Emacs", "bindings": {"File:Save": "Ctrl+Shift+S"}}.
# Outputs: QAction.setShortcut calls; a console line and a modal warning when two actions end up on the same key.
# Design decisions: the user file is read through app.security.read_text_file_safely (size cap, UTF-8 check) because it is user-editable;
# a broken file falls back to the built-in defaults instead of blocking startup.
# Known limits: binding strings are not validated (an unparsable one becomes an empty QKeySequence and the action has no shortcut),
# and collisions are only reported, not resolved. Qt itself decides which action wins an ambiguous shortcut. Needs PySide6, so it
# cannot be unit tested headless without a QApplication.

import os
import json
from PySide6.QtWidgets import QMessageBox
from PySide6.QtGui import QKeySequence
from app.security import read_text_file_safely

# Built-in shortcut profiles. Outer key = profile name shown to users; inner key = "Group:Action" id; value = Qt key-sequence string.
# A comma inside a value ("Ctrl+X,Ctrl+N") is a Qt chord: two key presses in a row (Emacs style).
# Every profile defines the same seven action ids so applying any one of them fully replaces the VS Code defaults
# (load_and_apply starts from VS Code and overlays the chosen profile).
# Ctrl+` is the backtick key, the same console toggle VS Code uses. Note that the Sublime profile binds Ctrl+B to both
# ToggleSidebar and RunCode (Ctrl+B is Sublime's own build key); the collision check below will warn about it whenever that profile is chosen.
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

# Reads shortcut settings once and pushes them onto the main window's QActions.
class KeybindingsEngine:

    def __init__(self, main_window):
        """Hold the main window whose actions get shortcuts and work out where keybindings.json lives.

        Args:
            main_window: the application's main window; it must already own the *_act QAction attributes used in load_and_apply.
        """
        self.main_window = main_window
        # The file lives three directories above this module (app/ide/keybindings.py -> project root), next to the project
        # files rather than in a per-user config folder, so it travels with the project and is easy for a student to find and edit.
        # Find keybindings.json in project root
        self.keybindings_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "keybindings.json"
        )

    def load_and_apply(self):
        """Resolve the final binding table (defaults, then preset, then user overrides), warn about collisions and apply it.

        Side effects: prints to stdout, may show a blocking QMessageBox, and calls setShortcut on seven actions.
        Errors reading or parsing keybindings.json are caught and printed; defaults stay in force.
        Called once by the main window after its actions exist.
        """
        # Layering order matters: start from the VS Code defaults so any action missing from a preset or user file still has a key.
        # 1. Start with VS Code defaults
        bindings = PRESETS["VS Code"].copy()
        
        # Overlay order is preset first, then the user's own "bindings", so a personal tweak always beats the named preset.
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
            # Catch-all on purpose: a malformed file (bad JSON, wrong types, unreadable, SecurityError for oversize) must never stop the IDE from starting.
            except Exception as e:
                print(f"Error loading keybindings.json: {e}")

        # Collision detection: normalise each sequence through QKeySequence.toString() first, so "ctrl+s" and "Ctrl+S" are seen as the same key.
        # reversed_bindings maps normalised key -> first action that claimed it; any later claimant is recorded as a collision.
        # 3. Collision detection
        reversed_bindings = {}
        collisions = []
        for action, key in bindings.items():
            # Empty or None value means "unbind this action"; skip it so the unbound actions do not collide with each other on "".
            if not key:
                continue
            norm_key = QKeySequence(key).toString()
            if norm_key in reversed_bindings:
                collisions.append((norm_key, reversed_bindings[norm_key], action))
            else:
                reversed_bindings[norm_key] = action

        # Report rather than silently pick a winner; the console copy helps when running without a visible window (tests, logs).
        if collisions:
            msg = "Keybinding collisions detected!\n\n"
            for key, act1, act2 in collisions:
                msg += f"  '{key}' is bound to both '{act1}' and '{act2}'\n"
            msg += "\nThe last binding will override the conflict."
            print(msg)
            # Shown after the console print so the user sees it even if the app was launched from a launcher with no terminal.
            # Show warning dialog
            QMessageBox.warning(self.main_window, "Keybinding Conflicts", msg)

        # Final mapping from action ids to concrete QActions. .get() returns None for ids that a user file removed, which apply_binding ignores.
        # 4. Map actions on main window
        self.apply_binding(self.main_window.new_file_act, bindings.get("File:New"))
        self.apply_binding(self.main_window.open_file_act, bindings.get("File:Open"))
        self.apply_binding(self.main_window.save_project_act, bindings.get("File:Save"))
        self.apply_binding(self.main_window.sidebar_toggle_act, bindings.get("Layout:ToggleSidebar"))
        self.apply_binding(self.main_window.console_toggle_act, bindings.get("Layout:ToggleConsole"))
        self.apply_binding(self.main_window.run_code_act, bindings.get("Editor:RunCode"))
        self.apply_binding(self.main_window.stop_code_act, bindings.get("Editor:StopCode"))

    def apply_binding(self, action, key_str):
        """Set one action's shortcut if both the action and a key string exist.

        Args:
            action: a QAction (or None if the main window lacks it).
            key_str: Qt key-sequence text such as "Ctrl+S" or a chord "Ctrl+X,Ctrl+S"; falsy leaves the action untouched.
        """
        if action and key_str:
            action.setShortcut(QKeySequence(key_str))
