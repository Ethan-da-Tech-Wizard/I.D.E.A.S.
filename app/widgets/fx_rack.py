from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSpinBox, QWidget

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

CHIPMUNK_SEMITONES = 7
DEEPEN_SEMITONES = -7


class FXRack(QWidget):
    """Distortion -> Delay -> Reverb toggle chain, plus a pitch control. Actual
    rendering lives in app.services.audio_fx (offline, cached per combination)."""

    fx_changed = Signal(bool, bool, bool)
    pitch_changed = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("FXRack")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.btn_distortion = QPushButton("Distortion")
        self.btn_delay = QPushButton("Delay")
        self.btn_reverb = QPushButton("Reverb")
        for btn in (self.btn_distortion, self.btn_delay, self.btn_reverb):
            btn.setCheckable(True)
            btn.setObjectName("fxToggle")
            btn.toggled.connect(self._emit_changed)
            layout.addWidget(btn)

        layout.addWidget(QLabel("Pitch:"))
        self.pitch_spin = QSpinBox()
        self.pitch_spin.setRange(-24, 24)
        self.pitch_spin.setValue(0)
        self.pitch_spin.setSuffix(" st")
        self.pitch_spin.valueChanged.connect(self.pitch_changed.emit)
        layout.addWidget(self.pitch_spin)

        self.btn_chipmunk = QPushButton("Chipmunk")
        self.btn_chipmunk.setObjectName("pitchPreset")
        self.btn_chipmunk.clicked.connect(lambda: self.pitch_spin.setValue(CHIPMUNK_SEMITONES))
        layout.addWidget(self.btn_chipmunk)

        self.btn_deepen = QPushButton("Deepen")
        self.btn_deepen.setObjectName("pitchPreset")
        self.btn_deepen.clicked.connect(lambda: self.pitch_spin.setValue(DEEPEN_SEMITONES))
        layout.addWidget(self.btn_deepen)

        self.btn_reset_pitch = QPushButton("0")
        self.btn_reset_pitch.setObjectName("pitchPreset")
        self.btn_reset_pitch.setFixedWidth(24)
        self.btn_reset_pitch.clicked.connect(lambda: self.pitch_spin.setValue(0))
        layout.addWidget(self.btn_reset_pitch)

    def _emit_changed(self, _checked: bool) -> None:
        self.fx_changed.emit(
            self.btn_distortion.isChecked(),
            self.btn_delay.isChecked(),
            self.btn_reverb.isChecked(),
        )
