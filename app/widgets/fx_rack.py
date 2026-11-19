# ==============================================================================
# Program: app/widgets/fx_rack.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: the compact effects strip shown above each DAW step sequencer track: three effect
# toggles (Distortion, Delay, Reverb), a pitch control in semitones with Chipmunk/Deepen/0 presets,
# a trigger-chance percentage and a disabled "Plugins (Coming Soon)" placeholder. It only reports
# user choices through signals; the actual sound processing is done elsewhere
# (app/services/audio_fx.py for the legacy offline effects and the engine in app/services/audio/).
# Used by: app/ui/daw.py (row 2 of each track widget; THEME_PRESETS feeds the DAW theme drop-down).
# Signals: fx_changed(distortion, delay, reverb) and pitch_changed(semitones).
# Note: chance_spin is a plain QSpinBox; daw.py connects its valueChanged signal and reads its value when saving.
# ==============================================================================

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSpinBox, QWidget

# Named accent colours (name -> hex) offered in the DAW theme drop-down; daw.py looks the colour up by name.
THEME_PRESETS = {
    "Tokyo": "#00f0ff",
    "Arctic": "#7ee8fa",
    "Chrome": "#c0c0c0",
    "Ember": "#ff5f1f",
    "Mint": "#3ddc97",
    "Violet": "#a26bfa",
    "Laser": "#ff007f",
    "Graphite": "#8a8a8a",
    "Signal": "#ffd23f",
    "Ruby": "#ff2d55",
}

# Preset pitch shifts in semitones: +7 (a fifth up) for a "chipmunk" voice, -7 for a deeper one.
CHIPMUNK_SEMITONES = 7
DEEPEN_SEMITONES = -7


class FXRack(QWidget):
    """Distortion -> Delay -> Reverb toggle chain, plus a pitch control. Actual
    rendering lives in app.services.audio_fx (offline, cached per combination)."""

    fx_changed = Signal(bool, bool, bool)
    pitch_changed = Signal(int)

    def __init__(self) -> None:
        """Build the horizontal strip of controls and connect their signals."""
        super().__init__()
        # Object name for styling by the DAW stylesheet.
        self.setObjectName("FXRack")
        layout = QHBoxLayout(self)
        # No outer padding: the strip sits tightly inside its track row.
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.btn_distortion = QPushButton("Distortion")
        self.btn_delay = QPushButton("Delay")
        self.btn_reverb = QPushButton("Reverb")
        # Checkable buttons act as on/off switches; any change re-emits the combined state through _emit_changed.
        for btn in (self.btn_distortion, self.btn_delay, self.btn_reverb):
            btn.setCheckable(True)
            btn.setObjectName("fxToggle")
            btn.toggled.connect(self._emit_changed)
            layout.addWidget(btn)

        layout.addWidget(QLabel("Pitch:"))
        # +/-24 semitones (two octaves) in either direction; valueChanged is forwarded as pitch_changed.
        self.pitch_spin = QSpinBox()
        self.pitch_spin.setRange(-24, 24)
        self.pitch_spin.setValue(0)
        self.pitch_spin.setSuffix(" st")
        self.pitch_spin.valueChanged.connect(self.pitch_changed.emit)
        layout.addWidget(self.pitch_spin)

        layout.addWidget(QLabel("Chance:"))
        # Probability (0-100 percent) that a step fires; 100 = always. daw.py listens to valueChanged and saves the value.
        self.chance_spin = QSpinBox()
        self.chance_spin.setRange(0, 100)
        self.chance_spin.setValue(100)
        self.chance_spin.setSuffix("%")
        layout.addWidget(self.chance_spin)

        # Presets write into the spin box, which then emits pitch_changed like a manual edit would.
        self.btn_chipmunk = QPushButton("Chipmunk")
        self.btn_chipmunk.setObjectName("pitchPreset")
        self.btn_chipmunk.clicked.connect(lambda: self.pitch_spin.setValue(CHIPMUNK_SEMITONES))
        layout.addWidget(self.btn_chipmunk)

        self.btn_deepen = QPushButton("Deepen")
        self.btn_deepen.setObjectName("pitchPreset")
        self.btn_deepen.clicked.connect(lambda: self.pitch_spin.setValue(DEEPEN_SEMITONES))
        layout.addWidget(self.btn_deepen)

        # Narrow button labelled "0" resets pitch to no shift.
        self.btn_reset_pitch = QPushButton("0")
        self.btn_reset_pitch.setObjectName("pitchPreset")
        self.btn_reset_pitch.setFixedWidth(24)
        self.btn_reset_pitch.clicked.connect(lambda: self.pitch_spin.setValue(0))
        layout.addWidget(self.btn_reset_pitch)

        # Placeholder for the VST3/AU plugin bridge (app/services/audio/plugin_bridge.py),
        # which is architecture-only today (stubbed scan/load, pass-through processing).
        # Disabled so it can't imply real plugin hosting is available yet.
        self.btn_plugins = QPushButton("Plugins (Coming Soon)")
        self.btn_plugins.setObjectName("pluginSlotPlaceholder")
        self.btn_plugins.setEnabled(False)
        self.btn_plugins.setToolTip(
            "VST3/AU plugin hosting is architected (app/services/audio/plugin_bridge.py) "
            "but not yet wired to real audio processing."
        )
        layout.addWidget(self.btn_plugins)

    def _emit_changed(self, _checked: bool) -> None:
        """Emit fx_changed with the current state of all three effect toggles.

        Args:
            _checked: the toggled button's new state (unused; all three states are re-read).
        """
        self.fx_changed.emit(
            self.btn_distortion.isChecked(),
            self.btn_delay.isChecked(),
            self.btn_reverb.isChecked(),
        )
