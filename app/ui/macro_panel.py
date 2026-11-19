# ==============================================================================
# Program: app/ui/macro_panel.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: the right-hand "Macros" panel that turns special comments in the script into live
# controls. A line like `# @param cutoff = 150 [20, 2000]` becomes a labelled slider+spin box; a
# bool becomes a checkbox; a string with options becomes a drop-down. Moving a control emits
# param_changed with ALL current values so a running script can react.
# Used by: app/application.py (calls update_params_from_code when the editor text changes and
# connects param_changed).
# Syntax: `# @param NAME = DEFAULT [MIN, MAX]` for numbers; `# @param NAME = "x" [a, b, c]` for
# choices; `# @param NAME = True` for booleans. Ints stay ints, a "." in the default makes a float.
# Design: widgets are rebuilt only when the SET OF PARAMETER NAMES changes, so typing elsewhere in
# the script does not reset a slider the user is dragging. Value changes are throttled to one
# emission per 50 ms.
# Known limit / bug noticed: if only a default, range or option list changes while the names stay
# the same, the controls are NOT updated (the early return in update_params_from_code).
# Tested by: tests/test_ide_core.py (test_macro_parsing).
# ==============================================================================

import re
import time
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QSlider, QCheckBox, 
    QComboBox, QSpinBox, QDoubleSpinBox, QScrollArea
)
from PySide6.QtCore import Qt, Signal, QTimer

class MacroPanel(QScrollArea):
    """Right-hand control panel with sliders/knobs generated dynamically from code parameters."""
    
    param_changed = Signal(dict) # Emits the entire dict of active parameters

    def __init__(self, parent=None):
        """Create the empty panel and the throttle timer.

        Args:
            parent: optional Qt parent.
        """
        super().__init__(parent)
        self.setObjectName("MacroPanel")
        # Lets the inner container follow the scroll area's width.
        self.setWidgetResizable(True)
        
        self.container = QWidget()
        self.layout = QVBoxLayout(self.container)
        self.layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.layout.setSpacing(15)
        self.setWidget(self.container)
        
        # values: name -> current value (what param_changed sends); controls: name -> widget(s) kept so they can be found/rebuilt.
        self.values = {}
        self.controls = {}
        
        # Dragging a slider fires valueChanged dozens of times; a single-shot timer coalesces the burst (see queue_value_change).
        # Throttling
        self.throttle_timer = QTimer(self)
        self.throttle_timer.setSingleShot(True)
        self.throttle_timer.timeout.connect(self.emit_change)
        self.last_emit_time = 0
        
        # Dark palette with cyan parameter titles; the "#0c0c12" background also covers child widgets so there are no light patches.
        self.setStyleSheet("""
            QScrollArea#MacroPanel {
                background-color: #0c0c12;
                border: none;
                border-left: 1px solid #1a1a28;
            }
            QWidget {
                background-color: #0c0c12;
                color: #d0d0d8;
            }
            QLabel#paramTitle {
                color: #00f0ff;
                font-weight: bold;
                font-size: 11px;
            }
            QLabel#paramValue {
                color: #a0a0c0;
                font-size: 11px;
            }
        """)

    def update_params_from_code(self, code):
        """Rebuild the controls from the @param comments in `code`.

        Args:
            code: full text of the script.
        """
        # parse_macros is pure text processing (no Qt).
        # 1. Parse parameters
        params = self.parse_macros(code)
        
        # Show usage help instead of an empty panel.
        # If no params found, show a placeholder
        if not params:
            self.clear_panel()
            placeholder = QLabel("No script @param definitions found.\n\nUse comments like:\n# @param cutoff = 150 [20, 2000]\n# @param volume = 0.8 [0.0, 1.0]\n# @param wave = \"sine\" [sine, saw]")
            placeholder.setStyleSheet("color: #4b5263; font-style: italic; padding: 20px;")
            placeholder.setWordWrap(True)
            self.layout.addWidget(placeholder)
            return
            
        # Same names -> keep existing widgets so a slider being dragged is not destroyed mid-drag (see the known limit in the file header).
        # 2. Check if the set of params is exactly the same to avoid rebuilding widgets (saves slider drag state!)
        current_keys = set(self.values.keys())
        new_keys = set(p["name"] for p in params)
        if current_keys == new_keys:
            # Just update defaults if they changed, otherwise preserve control state
            return
            
        # Start clean: delete old widgets and forget their values.
        # 3. Rebuild controls
        self.clear_panel()
        self.values.clear()
        self.controls.clear()
        
        for p in params:
            name = p["name"]
            default = p["default"]
            kind = p["kind"]
            
            self.values[name] = default
            
            # One vertical block per parameter: header (name and value) above the control.
            # Row container
            row = QWidget()
            row_layout = QVBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(4)
            
            # Header label (name + value)
            header = QWidget()
            header_layout = QHBoxLayout(header)
            header_layout.setContentsMargins(0, 0, 0, 0)
            
            lbl_name = QLabel(name)
            lbl_name.setObjectName("paramTitle")
            lbl_val = QLabel(str(default))
            lbl_val.setObjectName("paramValue")
            
            header_layout.addWidget(lbl_name)
            header_layout.addStretch()
            header_layout.addWidget(lbl_val)
            row_layout.addWidget(header)
            
            # Choose the control by kind: bool -> checkbox, str with options -> combo box, number with a range -> slider+spin box. Anything else shows only its value.
            # Control widget
            if kind == "bool":
                chk = QCheckBox("Enable")
                chk.setChecked(default)
                # Default arguments (n=name, l=lbl_val) bind the CURRENT loop values; a plain closure would see only the last parameter.
                chk.toggled.connect(lambda val, n=name, l=lbl_val: self.on_bool_changed(n, val, l))
                row_layout.addWidget(chk)
                self.controls[name] = chk
                
            elif kind == "str" and p["options"]:
                combo = QComboBox()
                combo.addItems(p["options"])
                combo.setCurrentText(default)
                combo.currentTextChanged.connect(lambda val, n=name, l=lbl_val: self.on_str_changed(n, val, l))
                row_layout.addWidget(combo)
                self.controls[name] = combo
                
            elif kind in ("int", "float") and p["min"] is not None and p["max"] is not None:
                min_v = p["min"]
                max_v = p["max"]
                
                # A slider for quick coarse changes and a spin box for exact typing, kept in sync both ways.
                # Slider + SpinBox layout
                ctrl_row = QWidget()
                ctrl_layout = QHBoxLayout(ctrl_row)
                ctrl_layout.setContentsMargins(0, 0, 0, 0)
                
                slider = QSlider(Qt.Orientation.Horizontal)
                slider.setMinimumWidth(100)
                
                if kind == "int":
                    spin = QSpinBox()
                    spin.setRange(int(min_v), int(max_v))
                    spin.setValue(int(default))
                    slider.setRange(int(min_v), int(max_v))
                    slider.setValue(int(default))
                    
                    # For ints, slider and spin box share the same range so each can write the other directly.
                    # Sync
                    slider.valueChanged.connect(spin.setValue)
                    spin.valueChanged.connect(slider.setValue)
                    spin.valueChanged.connect(lambda val, n=name, l=lbl_val: self.on_num_changed(n, val, l))
                    
                else: # float
                    spin = QDoubleSpinBox()
                    spin.setRange(float(min_v), float(max_v))
                    spin.setSingleStep(0.05)
                    spin.setValue(float(default))
                    
                    # QSlider only holds integers, so a float range is mapped onto 0..1000 steps (resolution = range/1000).
                    # Map float (min_v to max_v) to slider int (0 to 1000)
                    slider.setRange(0, 1000)
                    initial_slider = int((default - min_v) / (max_v - min_v) * 1000)
                    slider.setValue(initial_slider)
                    
                    # The two helpers convert between slider position and float value; the comparisons stop an endless slider<->spin update loop.
                    # Sync lambda with mapping
                    def sync_slider_to_spin(slider_val, s=spin, mn=min_v, mx=max_v):
                        f_val = mn + (slider_val / 1000.0) * (mx - mn)
                        if abs(s.value() - f_val) > 0.001:
                            s.setValue(f_val)
                            
                    def sync_spin_to_slider(spin_val, sl=slider, mn=min_v, mx=max_v):
                        s_val = int((spin_val - mn) / (mx - mn) * 1000.0)
                        if sl.value() != s_val:
                            sl.setValue(s_val)
                            
                    slider.valueChanged.connect(sync_slider_to_spin)
                    spin.valueChanged.connect(sync_spin_to_slider)
                    spin.valueChanged.connect(lambda val, n=name, l=lbl_val: self.on_num_changed(n, val, l))
                
                ctrl_layout.addWidget(slider, 1)
                ctrl_layout.addWidget(spin)
                row_layout.addWidget(ctrl_row)
                self.controls[name] = (slider, spin)
                
            # Unsupported combination (for example a plain string with no options or a number without a range).
            else:
                # Fallback simple text field/label
                lbl_val.setText(str(default))
                
            self.layout.addWidget(row)

    def clear_panel(self):
        """Remove and schedule deletion of every widget in the layout."""
        while self.layout.count():
            item = self.layout.takeAt(0)
            widget = item.widget()
            if widget:
                # deleteLater (not delete) lets Qt finish any events still queued for the widget.
                widget.deleteLater()

    def parse_macros(self, code):
        """Extract parameter definitions from `code`.

        Returns:
            list of dicts with keys name, default, kind ("int", "float", "bool", "str" or "text"),
            min, max (numbers or None) and options (list of strings).
        """
        params = []
        # Groups: 1 = name, 2 = default text (up to "[" or end of line), 3 = optional text inside [...].
        # Matches comments starting with # @param name = val [min, max] or [opt1, opt2]
        pattern = r"#\s*@param\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*([^\[\n\r]+)(?:\s*\[(.*?)\])?"
        for match in re.finditer(pattern, code):
            name = match.group(1).strip()
            default_raw = match.group(2).strip()
            range_raw = match.group(3)
            
            val = None
            kind = "text"
            # Type inference order: bool, then float (contains "."), then int, otherwise a string with quotes stripped.
            if default_raw.lower() in ("true", "false"):
                val = default_raw.lower() == "true"
                kind = "bool"
            else:
                try:
                    if "." in default_raw:
                        val = float(default_raw)
                        kind = "float"
                    else:
                        val = int(default_raw)
                        kind = "int"
                except ValueError:
                    val = default_raw.strip('"').strip("'")
                    kind = "str"
            
            # Bracket text is a numeric range for int/float, otherwise a list of options for a choice control.
            options = []
            min_val, max_val = None, None
            if range_raw:
                parts = [p.strip() for p in range_raw.split(",")]
                if kind in ("int", "float"):
                    if len(parts) == 2:
                        try:
                            # Only a well-formed two-value range is accepted; a malformed one leaves min/max None (no slider is built).
                            min_val = float(parts[0]) if kind == "float" else int(parts[0])
                            max_val = float(parts[1]) if kind == "float" else int(parts[1])
                        except ValueError:
                            pass
                else:
                    options = [p.strip().strip('"').strip("'") for p in parts]
            
            params.append({
                "name": name,
                "default": val,
                "kind": kind,
                "min": min_val,
                "max": max_val,
                "options": options
            })
        return params

    def on_bool_changed(self, name, val, label):
        """Update the value label and queue the change."""
        label.setText(str(val))
        self.queue_value_change(name, val)

    def on_str_changed(self, name, val, label):
        """Update the value label and queue the change."""
        label.setText(val)
        self.queue_value_change(name, val)

    def on_num_changed(self, name, val, label):
        """Update the value label (floats shown with 2 decimals) and queue the change."""
        label.setText(f"{val:.2f}" if isinstance(val, float) else str(val))
        self.queue_value_change(name, val)

    def queue_value_change(self, name, val):
        """Store the new value and emit param_changed at most once per 50 ms."""
        self.values[name] = val
        # If 50 ms have passed since the last emission, emit immediately (first change feels instant); otherwise ensure a timer will emit the latest values shortly.
        now = time.time()
        if now - self.last_emit_time >= 0.05: # 50ms throttle
            self.emit_change()
        else:
            if not self.throttle_timer.isActive():
                self.throttle_timer.start(50)

    def emit_change(self):
        """Emit param_changed with a copy of the values (copy so receivers cannot mutate panel state)."""
        self.last_emit_time = time.time()
        self.param_changed.emit(self.values.copy())
