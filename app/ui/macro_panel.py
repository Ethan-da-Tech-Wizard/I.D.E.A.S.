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
        super().__init__(parent)
        self.setObjectName("MacroPanel")
        self.setWidgetResizable(True)
        
        self.container = QWidget()
        self.layout = QVBoxLayout(self.container)
        self.layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.layout.setSpacing(15)
        self.setWidget(self.container)
        
        self.values = {}
        self.controls = {}
        
        # Throttling
        self.throttle_timer = QTimer(self)
        self.throttle_timer.setSingleShot(True)
        self.throttle_timer.timeout.connect(self.emit_change)
        self.last_emit_time = 0
        
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
        # 1. Parse parameters
        params = self.parse_macros(code)
        
        # If no params found, show a placeholder
        if not params:
            self.clear_panel()
            placeholder = QLabel("No script @param definitions found.\n\nUse comments like:\n# @param cutoff = 150 [20, 2000]\n# @param volume = 0.8 [0.0, 1.0]\n# @param wave = \"sine\" [sine, saw]")
            placeholder.setStyleSheet("color: #4b5263; font-style: italic; padding: 20px;")
            placeholder.setWordWrap(True)
            self.layout.addWidget(placeholder)
            return
            
        # 2. Check if the set of params is exactly the same to avoid rebuilding widgets (saves slider drag state!)
        current_keys = set(self.values.keys())
        new_keys = set(p["name"] for p in params)
        if current_keys == new_keys:
            # Just update defaults if they changed, otherwise preserve control state
            return
            
        # 3. Rebuild controls
        self.clear_panel()
        self.values.clear()
        self.controls.clear()
        
        for p in params:
            name = p["name"]
            default = p["default"]
            kind = p["kind"]
            
            self.values[name] = default
            
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
            
            # Control widget
            if kind == "bool":
                chk = QCheckBox("Enable")
                chk.setChecked(default)
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
                    
                    # Sync
                    slider.valueChanged.connect(spin.setValue)
                    spin.valueChanged.connect(slider.setValue)
                    spin.valueChanged.connect(lambda val, n=name, l=lbl_val: self.on_num_changed(n, val, l))
                    
                else: # float
                    spin = QDoubleSpinBox()
                    spin.setRange(float(min_v), float(max_v))
                    spin.setSingleStep(0.05)
                    spin.setValue(float(default))
                    
                    # Map float (min_v to max_v) to slider int (0 to 1000)
                    slider.setRange(0, 1000)
                    initial_slider = int((default - min_v) / (max_v - min_v) * 1000)
                    slider.setValue(initial_slider)
                    
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
                
            else:
                # Fallback simple text field/label
                lbl_val.setText(str(default))
                
            self.layout.addWidget(row)

    def clear_panel(self):
        while self.layout.count():
            item = self.layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

    def parse_macros(self, code):
        params = []
        # Matches comments starting with # @param name = val [min, max] or [opt1, opt2]
        pattern = r"#\s*@param\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*([^\[\n\r]+)(?:\s*\[(.*?)\])?"
        for match in re.finditer(pattern, code):
            name = match.group(1).strip()
            default_raw = match.group(2).strip()
            range_raw = match.group(3)
            
            val = None
            kind = "text"
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
            
            options = []
            min_val, max_val = None, None
            if range_raw:
                parts = [p.strip() for p in range_raw.split(",")]
                if kind in ("int", "float"):
                    if len(parts) == 2:
                        try:
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
        label.setText(str(val))
        self.queue_value_change(name, val)

    def on_str_changed(self, name, val, label):
        label.setText(val)
        self.queue_value_change(name, val)

    def on_num_changed(self, name, val, label):
        label.setText(f"{val:.2f}" if isinstance(val, float) else str(val))
        self.queue_value_change(name, val)

    def queue_value_change(self, name, val):
        self.values[name] = val
        now = time.time()
        if now - self.last_emit_time >= 0.05: # 50ms throttle
            self.emit_change()
        else:
            if not self.throttle_timer.isActive():
                self.throttle_timer.start(50)

    def emit_change(self):
        self.last_emit_time = time.time()
        self.param_changed.emit(self.values.copy())
