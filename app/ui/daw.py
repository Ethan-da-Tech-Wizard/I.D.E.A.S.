import math
import os
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QScrollArea, QComboBox, QSlider, QColorDialog, QLineEdit,
    QInputDialog, QMessageBox
)
from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtMultimedia import QSoundEffect
from PySide6.QtGui import QFont, QColor
from app.security import SecurityError, list_bundled_samples, resolve_sample_path
from app.services.audio_export import ExportTrack, export_pattern_wav
from app.services.audio_fx import get_or_render_fx
from app.widgets.fx_rack import FXRack, THEME_PRESETS
from app.storage.database import StorageError, list_patterns, load_pattern, save_pattern

class DawTrack(QWidget):
    def __init__(self, samples_dict, samples_base_dir, samples_flat=None):
        super().__init__()
        self.setObjectName("DawTrack")
        self.steps = [False] * 16
        self.samples_dict = samples_dict
        self.samples_base_dir = samples_base_dir
        self.samples_flat = samples_flat or []
        self.current_relative_path = None
        self.fx_state = (False, False, False)
        self.octave_semitones = 0
        self.tuning_offset_semitones = 0.0
        self.search_active = False

        self.sound = None

        self.setup_ui()
        
    def setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        
        # Track controls (Left)
        controls_layout = QVBoxLayout()
        controls_layout.setSpacing(5)

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search all sounds…")
        self.search_box.textChanged.connect(self.on_search_changed)

        self.category_combo = QComboBox()
        self.category_combo.addItems(sorted(self.samples_dict.keys()))
        self.category_combo.currentTextChanged.connect(self.on_category_changed)

        self.sample_combo = QComboBox()
        self.sample_combo.currentTextChanged.connect(self.on_sample_changed)

        self.fx_rack = FXRack()
        self.fx_rack.fx_changed.connect(self.on_fx_changed)
        self.fx_rack.pitch_changed.connect(self.on_pitch_changed)

        controls_layout.addWidget(self.search_box)
        controls_layout.addWidget(self.category_combo)
        controls_layout.addWidget(self.sample_combo)
        controls_layout.addWidget(self.fx_rack)
        layout.addLayout(controls_layout, 1)
        
        # Initialize comboboxes
        if self.samples_dict:
            first_cat = sorted(self.samples_dict.keys())[0]
            self.category_combo.setCurrentText(first_cat)
            self.on_category_changed(first_cat)
            
        # 16-Step Grid (Right)
        self.step_buttons = []
        grid_layout = QHBoxLayout()
        grid_layout.setSpacing(2)
        
        for i in range(16):
            btn = QPushButton()
            btn.setFixedSize(30, 40)
            btn.setObjectName("stepBtn")
            btn.setProperty("active", "false")
            btn.setProperty("playing", "false")
            btn.clicked.connect(lambda checked, idx=i: self.toggle_step(idx))
            self.step_buttons.append(btn)
            grid_layout.addWidget(btn)
            
            # Add small gap every 4 steps (1 beat)
            if (i + 1) % 4 == 0 and i != 15:
                grid_layout.addSpacing(10)
                
        layout.addLayout(grid_layout, 4)
        
    def on_category_changed(self, category):
        self.sample_combo.blockSignals(True)
        self.sample_combo.clear()
        if category in self.samples_dict:
            self.sample_combo.addItems(sorted(self.samples_dict[category]))
        self.sample_combo.blockSignals(False)
        if self.sample_combo.count():
            self.sample_combo.setCurrentIndex(0)
            self.on_sample_changed(self.sample_combo.currentText())

    def on_sample_changed(self, sample_name):
        if not sample_name: return
        if self.search_active:
            self.current_relative_path = sample_name
        else:
            category = self.category_combo.currentText()
            self.current_relative_path = f"{category}/{sample_name}" if category else sample_name
        self.refresh_audio_source()

    def on_search_changed(self, text):
        text = text.strip().lower()
        self.search_active = bool(text)
        self.category_combo.setEnabled(not self.search_active)
        if not self.search_active:
            self.on_category_changed(self.category_combo.currentText())
            return
        matches = sorted(p for p in self.samples_flat if text in p.lower())
        self.sample_combo.blockSignals(True)
        self.sample_combo.clear()
        self.sample_combo.addItems(matches)
        self.sample_combo.blockSignals(False)
        if matches:
            self.sample_combo.setCurrentIndex(0)
            self.on_sample_changed(self.sample_combo.currentText())

    def on_fx_changed(self, distortion, delay, reverb):
        self.fx_state = (distortion, delay, reverb)
        self.refresh_audio_source()

    def on_pitch_changed(self, semitones):
        self.octave_semitones = semitones
        self.refresh_audio_source()

    def set_tuning_offset(self, offset_semitones: float) -> None:
        """Applied on top of the per-track pitch control by the workspace's
        global 432/528 Hz tuning toggles, so 'reference pitch' and 'creative
        pitch shift' stay independent controls."""
        self.tuning_offset_semitones = offset_semitones
        self.refresh_audio_source()

    def refresh_audio_source(self):
        if not self.current_relative_path:
            return
        distortion, delay, reverb = self.fx_state
        try:
            file_path = get_or_render_fx(
                self.samples_base_dir, self.current_relative_path,
                distortion=distortion, delay=delay, reverb=reverb,
                semitones=self.octave_semitones + self.tuning_offset_semitones,
            )
        except SecurityError:
            if self.sound is not None:
                self.sound.setSource(QUrl())
            return
        self.ensure_sound()
        self.sound.setSource(QUrl.fromLocalFile(file_path))
            
    def toggle_step(self, index):
        self.steps[index] = not self.steps[index]
        btn = self.step_buttons[index]
        btn.setProperty("active", "true" if self.steps[index] else "false")
        self.force_style_update(btn)
        
    def update_playhead(self, step_index):
        # Visually highlight the current step, and trigger audio if active
        for i, btn in enumerate(self.step_buttons):
            if i == step_index:
                btn.setProperty("playing", "true")
                if self.steps[i] and self.sound is not None:
                    self.sound.play()
            else:
                btn.setProperty("playing", "false")
            self.force_style_update(btn)
            
    def force_style_update(self, widget):
        widget.style().unpolish(widget)
        widget.style().polish(widget)

    def ensure_sound(self):
        if self.sound is None:
            self.sound = QSoundEffect()
            self.sound.setVolume(0.8)

    def to_dict(self) -> dict:
        return {
            "relative_path": self.current_relative_path,
            "fx_state": list(self.fx_state),
            "pitch_semitones": self.octave_semitones,
            "steps": list(self.steps),
        }

    def restore_state(self, state: dict) -> None:
        relative_path = state.get("relative_path")
        if relative_path:
            category, _, filename = relative_path.rpartition("/")
            if category in self.samples_dict:
                self.search_box.blockSignals(True)
                self.search_box.clear()
                self.search_box.blockSignals(False)
                self.search_active = False
                self.category_combo.setEnabled(True)
                self.category_combo.blockSignals(True)
                self.category_combo.setCurrentText(category)
                self.category_combo.blockSignals(False)
                self.sample_combo.blockSignals(True)
                self.sample_combo.clear()
                self.sample_combo.addItems(sorted(self.samples_dict[category]))
                if filename in self.samples_dict[category]:
                    self.sample_combo.setCurrentText(filename)
                self.sample_combo.blockSignals(False)
            self.current_relative_path = relative_path

        distortion, delay, reverb = state.get("fx_state", [False, False, False])
        self.fx_state = (bool(distortion), bool(delay), bool(reverb))
        self.fx_rack.btn_distortion.setChecked(self.fx_state[0])
        self.fx_rack.btn_delay.setChecked(self.fx_state[1])
        self.fx_rack.btn_reverb.setChecked(self.fx_state[2])

        semitones = state.get("pitch_semitones", state.get("octave", 0) * 12)
        self.octave_semitones = semitones
        self.fx_rack.pitch_spin.setValue(semitones)

        steps = state.get("steps", [False] * 16)
        for i, active in enumerate(steps[:16]):
            self.steps[i] = bool(active)
            btn = self.step_buttons[i]
            btn.setProperty("active", "true" if active else "false")
            self.force_style_update(btn)

        self.refresh_audio_source()

from PySide6.QtCore import Qt, QTimer, QUrl, Signal, QThread
import time

class AudioEngineThread(QThread):
    step_triggered = Signal(int)

    def __init__(self):
        super().__init__()
        self.is_playing = False
        self.bpm = 120
        self.current_step = 0

    def run(self):
        self.is_playing = True
        self.current_step = 0
        while self.is_playing:
            # High-resolution time tracking for sample-accurate scheduling
            start_time = time.perf_counter()
            
            # Emit signal to main thread to update UI and trigger audio
            self.step_triggered.emit(self.current_step)
            
            self.current_step = (self.current_step + 1) % 16
            
            # Calculate sleep duration (16th notes)
            interval_sec = (60.0 / self.bpm) / 4.0
            
            # Sleep until the exact interval has passed (busy wait for sub-millisecond precision)
            while (time.perf_counter() - start_time) < interval_sec:
                if not self.is_playing:
                    break
                time.sleep(0.001)

    def stop(self):
        self.is_playing = False
        self.wait()

STANDARD_TUNING_HZ = 440.0


class DawWorkspace(QWidget):
    theme_changed = Signal(str)

    def __init__(self):
        super().__init__()
        self.is_playing = False
        self.bpm = 120
        self.tuning_hz = STANDARD_TUNING_HZ
        self.tuning_offset_semitones = 0.0

        self.engine_thread = AudioEngineThread()
        self.engine_thread.step_triggered.connect(self.step_forward)
        
        self.samples_base_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "resources", "samples")
        self.samples_flat = self.get_available_samples_flat()
        self.samples_dict = self.group_samples(self.samples_flat)
        self.tracks = []

        self.setup_ui()

    def get_available_samples_flat(self) -> list[str]:
        try:
            return list_bundled_samples(self.samples_base_dir)
        except (OSError, SecurityError):
            return []

    def group_samples(self, relative_paths: list[str]) -> dict:
        samples: dict[str, list[str]] = {}
        for relative_path in relative_paths:
            category, _, filename = relative_path.rpartition("/")
            samples.setdefault(category, []).append(filename)
        return samples
        
    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        
        # --- TOP TRANSPORT BAR ---
        transport_layout = QHBoxLayout()
        
        self.play_btn = QPushButton("PLAY")
        self.play_btn.setFixedSize(100, 40)
        self.play_btn.clicked.connect(self.toggle_playback)
        
        bpm_label = QLabel("BPM:")
        self.bpm_slider = QSlider(Qt.Orientation.Horizontal)
        self.bpm_slider.setRange(60, 200)
        self.bpm_slider.setValue(self.bpm)
        self.bpm_slider.setFixedWidth(200)
        self.bpm_slider.valueChanged.connect(self.update_bpm)
        
        self.bpm_display = QLabel(f"{self.bpm}")
        self.bpm_display.setFixedWidth(30)

        theme_label = QLabel("Theme:")
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(list(THEME_PRESETS.keys()) + ["Custom"])
        self.theme_combo.currentTextChanged.connect(self.apply_theme)

        tuning_label = QLabel("Tuning:")
        self.btn_tuning_432 = QPushButton("432 Hz")
        self.btn_tuning_432.setCheckable(True)
        self.btn_tuning_432.setObjectName("tuningToggle")
        self.btn_tuning_432.setToolTip(
            "Retune all sample playback and exports so A4 = 432 Hz (standard is A4 = 440 Hz)."
        )
        self.btn_tuning_432.toggled.connect(self.on_tuning_432_toggled)

        self.btn_tuning_528 = QPushButton("528 Hz")
        self.btn_tuning_528.setCheckable(True)
        self.btn_tuning_528.setObjectName("tuningToggle")
        self.btn_tuning_528.setToolTip(
            "Retune all sample playback and exports so A4 = 528 Hz (standard is A4 = 440 Hz)."
        )
        self.btn_tuning_528.toggled.connect(self.on_tuning_528_toggled)

        self.save_btn = QPushButton("Save Pattern")
        self.save_btn.clicked.connect(self.save_pattern_dialog)
        self.load_btn = QPushButton("Load Pattern")
        self.load_btn.clicked.connect(self.load_pattern_dialog)
        
        self.export_btn = QPushButton("Export .wav")
        self.export_btn.setObjectName("exportBtn")
        self.export_btn.clicked.connect(self.export_audio)

        transport_layout.addWidget(self.play_btn)
        transport_layout.addSpacing(20)
        transport_layout.addWidget(bpm_label)
        transport_layout.addWidget(self.bpm_slider)
        transport_layout.addWidget(self.bpm_display)
        transport_layout.addSpacing(20)
        transport_layout.addWidget(theme_label)
        transport_layout.addWidget(self.theme_combo)
        transport_layout.addSpacing(20)
        transport_layout.addWidget(tuning_label)
        transport_layout.addWidget(self.btn_tuning_432)
        transport_layout.addWidget(self.btn_tuning_528)
        transport_layout.addSpacing(20)
        transport_layout.addWidget(self.save_btn)
        transport_layout.addWidget(self.load_btn)
        transport_layout.addSpacing(10)
        transport_layout.addWidget(self.export_btn)
        transport_layout.addStretch()
        
        layout.addLayout(transport_layout)
        
        # --- TRACKS AREA ---
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QScrollArea.Shape.NoFrame)
        
        self.tracks_container = QWidget()
        self.tracks_layout = QVBoxLayout(self.tracks_container)
        self.tracks_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        
        scroll_area.setWidget(self.tracks_container)
        layout.addWidget(scroll_area, 1)
        
        # Add Track Button
        add_track_btn = QPushButton("+ ADD TRACK")
        add_track_btn.setFixedSize(120, 30)
        add_track_btn.clicked.connect(self.add_track)
        layout.addWidget(add_track_btn, 0, Qt.AlignmentFlag.AlignCenter)
        
        # Add 4 default tracks
        for _ in range(4):
            self.add_track()
            
    def add_track(self):
        track = DawTrack(self.samples_dict, self.samples_base_dir, self.samples_flat)
        track.tuning_offset_semitones = self.tuning_offset_semitones
        self.tracks.append(track)
        self.tracks_layout.addWidget(track)

    def on_tuning_432_toggled(self, checked: bool) -> None:
        if checked:
            self.btn_tuning_528.setChecked(False)
            self.apply_tuning(432.0)
        elif not self.btn_tuning_528.isChecked():
            self.apply_tuning(STANDARD_TUNING_HZ)

    def on_tuning_528_toggled(self, checked: bool) -> None:
        if checked:
            self.btn_tuning_432.setChecked(False)
            self.apply_tuning(528.0)
        elif not self.btn_tuning_432.isChecked():
            self.apply_tuning(STANDARD_TUNING_HZ)

    def apply_tuning(self, tuning_hz: float) -> None:
        self.tuning_hz = tuning_hz
        self.tuning_offset_semitones = 12 * math.log2(tuning_hz / STANDARD_TUNING_HZ)
        for track in self.tracks:
            track.set_tuning_offset(self.tuning_offset_semitones)

    def sync_tuning_buttons(self, tuning_hz: float) -> None:
        self.btn_tuning_432.blockSignals(True)
        self.btn_tuning_528.blockSignals(True)
        self.btn_tuning_432.setChecked(tuning_hz == 432.0)
        self.btn_tuning_528.setChecked(tuning_hz == 528.0)
        self.btn_tuning_432.blockSignals(False)
        self.btn_tuning_528.blockSignals(False)

    def save_pattern_dialog(self) -> None:
        name, ok = QInputDialog.getText(self, "Save Pattern", "Pattern name:")
        name = name.strip()
        if not ok or not name:
            return
        try:
            save_pattern(name, self.bpm, [track.to_dict() for track in self.tracks])
        except StorageError as exc:
            QMessageBox.warning(self, "Save Failed", str(exc))

    def load_pattern_dialog(self) -> None:
        names = list_patterns()
        if not names:
            QMessageBox.information(self, "Load Pattern", "No saved patterns yet.")
            return
        name, ok = QInputDialog.getItem(self, "Load Pattern", "Choose a pattern:", names, editable=False)
        if not ok or not name:
            return
        data = load_pattern(name)
        if data is None:
            return
        self.bpm = data["bpm"]
        self.bpm_slider.setValue(self.bpm)
        self.engine_thread.bpm = self.bpm
        tracks_state = data["tracks"]
        while len(self.tracks) < len(tracks_state):
            self.add_track()
        for track, state in zip(self.tracks, tracks_state):
            track.restore_state(state)

    def apply_theme(self, theme_name: str) -> None:
        if theme_name == "Custom":
            color = QColorDialog.getColor(QColor("#007acc"), self, "Choose Accent Color")
            if not color.isValid():
                return
            accent = color.name()
        else:
            accent = THEME_PRESETS.get(theme_name)
            if accent is None:
                return
        self.theme_changed.emit(accent)
        
    def update_bpm(self, value):
        self.bpm = value
        self.bpm_display.setText(str(self.bpm))
        self.engine_thread.bpm = self.bpm
            
    def toggle_playback(self):
        if self.is_playing:
            self.play_btn.setText("PLAY")
            self.is_playing = False
            self.engine_thread.stop()
            # Clear playhead
            for track in self.tracks:
                track.update_playhead(-1)
        else:
            self.play_btn.setText("STOP")
            self.is_playing = True
            self.engine_thread.bpm = self.bpm
            self.engine_thread.start(QThread.Priority.TimeCriticalPriority)
            
    def step_forward(self, step_index):
        if not self.is_playing: return
        
        for track in self.tracks:
            track.update_playhead(step_index)

    def get_state(self) -> dict:
        state = {
            "bpm": self.bpm,
            "theme": self.theme_combo.currentText(),
            "tuning_hz": self.tuning_hz,
            "tracks": []
        }
        for track in self.tracks:
            category = track.category_combo.currentText()
            sample = track.sample_combo.currentText()
            track_data = {
                "category": category,
                "sample": sample,
                "steps": track.steps.copy(),
                "fx": {
                    "distortion": track.fx_rack.btn_distortion.isChecked(),
                    "delay": track.fx_rack.btn_delay.isChecked(),
                    "reverb": track.fx_rack.btn_reverb.isChecked()
                }
            }
            state["tracks"].append(track_data)
        return state

    def load_state(self, state: dict):
        if "bpm" in state:
            self.bpm_slider.setValue(state["bpm"])
        if "theme" in state:
            self.theme_combo.setCurrentText(state["theme"])
            
        if "tracks" in state:
            # Clear existing tracks
            for track in self.tracks:
                self.tracks_layout.removeWidget(track)
                track.deleteLater()
            self.tracks.clear()
            
            # Load saved tracks
            for track_data in state["tracks"]:
                track = DawTrack(self.samples_dict, self.samples_base_dir)
                if track_data.get("category"):
                    track.category_combo.setCurrentText(track_data["category"])
                if track_data.get("sample"):
                    track.sample_combo.setCurrentText(track_data["sample"])
                    
                if "steps" in track_data:
                    for i, active in enumerate(track_data["steps"]):
                        if active and i < 16:
                            track.toggle_step(i)
                            
                if "fx" in track_data:
                    fx = track_data["fx"]
                    track.fx_rack.btn_distortion.setChecked(fx.get("distortion", False))
                    track.fx_rack.btn_delay.setChecked(fx.get("delay", False))
                    track.fx_rack.btn_reverb.setChecked(fx.get("reverb", False))
                    
                self.tracks.append(track)
                self.tracks_layout.addWidget(track)

        tuning_hz = state.get("tuning_hz", STANDARD_TUNING_HZ)
        self.sync_tuning_buttons(tuning_hz)
        self.apply_tuning(tuning_hz)

    def export_audio(self):
        from PySide6.QtWidgets import QFileDialog
        
        file_path, _ = QFileDialog.getSaveFileName(self, "Export Audio", os.path.expanduser("~"), "WAV Audio (*.wav)")
        if not file_path:
            return

        export_tracks = [
            ExportTrack(
                relative_path=track.current_relative_path,
                steps=list(track.steps),
                distortion=track.fx_state[0],
                delay=track.fx_state[1],
                reverb=track.fx_state[2],
                semitones=track.octave_semitones + track.tuning_offset_semitones,
            )
            for track in self.tracks
        ]
        try:
            exported_path = export_pattern_wav(file_path, self.samples_base_dir, self.bpm, export_tracks)
            QMessageBox.information(self, "Export Complete", f"Successfully bounced beat to:\n{exported_path}")
        except SecurityError as exc:
            QMessageBox.warning(self, "Export Blocked", str(exc))
        except Exception as exc:
            QMessageBox.critical(self, "Export Failed", f"Could not export audio:\n{exc}")
