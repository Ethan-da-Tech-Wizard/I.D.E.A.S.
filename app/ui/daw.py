import math
import os
import random
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QScrollArea, QComboBox, QSlider, QColorDialog, QLineEdit,
    QInputDialog, QMessageBox, QSizePolicy, QGridLayout, QFrame
)
from PySide6.QtCore import Qt, QTimer, QUrl, Signal, QObject
from PySide6.QtGui import QFont, QColor
from app.security import SecurityError, list_bundled_samples, resolve_sample_path
from app.services.audio_export import ExportTrack, export_pattern_wav
from app.services.audio.dsp import load_and_prepare_sample
from app.services.audio.engine import AudioEngine
from app.services.audio.track_engine import (
    LEGACY_DELAY_FEEDBACK,
    LEGACY_DELAY_MIX,
    LEGACY_DELAY_MS,
    LEGACY_DISTORTION_DRIVE,
    LEGACY_REVERB_DECAY,
    LEGACY_REVERB_MIX,
    TrackProcessor,
    TrackState,
)
from app.widgets.fx_rack import FXRack, THEME_PRESETS
from app.storage.database import StorageError, list_patterns, load_pattern, save_pattern

_EMPTY_STEREO_SAMPLE = None  # populated lazily by _silent_sample() -- see below


def _silent_sample():
    """A minimal valid (1-frame, silent) stereo sample so a DawTrack can own
    a TrackProcessor before the user has picked a real sample yet.
    TrackProcessor requires sample_data at construction; deferring
    construction until a real sample is chosen would mean mute/solo/FX
    state has nowhere to live in the meantime."""
    global _EMPTY_STEREO_SAMPLE
    if _EMPTY_STEREO_SAMPLE is None:
        import numpy as np
        _EMPTY_STEREO_SAMPLE = np.zeros((1, 2), dtype=np.float32)
    return _EMPTY_STEREO_SAMPLE

CUSTOM_SAMPLE_CATEGORY = "My Samples"

class NoScrollComboBox(QComboBox):
    """QComboBox that ignores mouse wheel events to prevent accidental
    value changes while scrolling the DAW panel.
    """
    def wheelEvent(self, event):
        event.ignore()

class DawTrack(QWidget):
    delete_requested = Signal(object)

    def __init__(self, samples_dict, samples_base_dir, samples_flat=None):
        super().__init__()
        self.setObjectName("DawTrack")
        self.steps = [False] * 128
        self.samples_dict = samples_dict
        self.samples_base_dir = samples_base_dir
        self.samples_flat = samples_flat or []
        self.current_relative_path = None
        self.fx_state = (False, False, False)
        self.octave_semitones = 0
        self.tuning_offset_semitones = 0.0
        self.search_active = False
        self.workspace_path = None  # Set by DawWorkspace.set_workspace_path() when a project is open.

        # Set by DawWorkspace right after construction, same convention as
        # workspace_path/tuning_offset_semitones above -- see
        # docs/DAW_VISION_SPEC.md's "Architecture Decision: Live Audio
        # Backend". track_id is this track's registration key with the
        # engine; unique for the lifetime of this widget.
        self.audio_engine: AudioEngine | None = None
        self.track_id: str = str(id(self))
        self.track_processor: TrackProcessor | None = None
        # Plain-attribute mirrors of Qt widget state, safe to read from the
        # audio callback thread per the Real-Time Safety Contract (no Qt
        # widget method may be called off the GUI thread) -- chance_pct
        # mirrors fx_rack.chance_spin, mute/solo mirror onto
        # track_processor.muted/.soloed directly since those already exist.
        self.chance_pct: int = 100

        self._last_playhead = -1

        self.setup_ui()
        
    def setup_ui(self):
        # Main vertical layout: controls on top, step grid below
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)
        
        # ── Row 1: Sample selection (search + category + sample) ──
        sample_row = QHBoxLayout()
        sample_row.setSpacing(6)

        self.btn_mute = QPushButton("M")
        self.btn_mute.setCheckable(True)
        self.btn_mute.setFixedWidth(28)
        self.btn_mute.setStyleSheet("""
            QPushButton {
                background-color: #1e1e2e;
                color: #a6adc8;
                border: 1px solid #313244;
                font-weight: bold;
                border-radius: 4px;
            }
            QPushButton:checked {
                background-color: #f38ba8;
                color: #11111b;
                border: 1px solid #f38ba8;
            }
        """)
        self.btn_mute.setToolTip("Mute Track")
        self.btn_mute.toggled.connect(self._on_mute_toggled)

        self.btn_solo = QPushButton("S")
        self.btn_solo.setCheckable(True)
        self.btn_solo.setFixedWidth(28)
        self.btn_solo.setStyleSheet("""
            QPushButton {
                background-color: #1e1e2e;
                color: #a6adc8;
                border: 1px solid #313244;
                font-weight: bold;
                border-radius: 4px;
            }
            QPushButton:checked {
                background-color: #f9e2af;
                color: #11111b;
                border: 1px solid #f9e2af;
            }
        """)
        self.btn_solo.setToolTip("Solo Track")
        self.btn_solo.toggled.connect(self._on_solo_toggled)

        self.btn_delete = QPushButton("🗑️")
        self.btn_delete.setFixedWidth(28)
        self.btn_delete.setStyleSheet("""
            QPushButton {
                background-color: #1e1e2e;
                color: #f38ba8;
                border: 1px solid #313244;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #f38ba8;
                color: #11111b;
                border: 1px solid #f38ba8;
            }
        """)
        self.btn_delete.setToolTip("Delete Track")
        self.btn_delete.clicked.connect(lambda: self.delete_requested.emit(self))

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("🔍 Search sounds…")
        self.search_box.setMinimumWidth(120)
        self.search_box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.search_box.textChanged.connect(self.on_search_changed)

        self.category_combo = NoScrollComboBox()
        self.category_combo.setMinimumWidth(100)
        self.category_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.category_combo.addItems(sorted(self.samples_dict.keys()))
        self.category_combo.currentTextChanged.connect(self.on_category_changed)

        self.sample_combo = NoScrollComboBox()
        self.sample_combo.setMinimumWidth(140)
        self.sample_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.sample_combo.currentTextChanged.connect(self.on_sample_changed)

        self.btn_randomize = QPushButton("🎲")
        self.btn_randomize.setToolTip("Randomly populate steps with active triggers")
        self.btn_randomize.setFixedWidth(36)
        self.btn_randomize.clicked.connect(self.randomize_steps)

        sample_row.addWidget(self.btn_mute)
        sample_row.addWidget(self.btn_solo)
        sample_row.addWidget(self.btn_delete)
        sample_row.addWidget(self.search_box, 2)
        sample_row.addWidget(self.category_combo, 2)
        sample_row.addWidget(self.sample_combo, 3)
        sample_row.addWidget(self.btn_randomize)

        layout.addLayout(sample_row)

        # ── Row 2: FX rack ──
        self.fx_rack = FXRack()
        self.fx_rack.fx_changed.connect(self.on_fx_changed)
        self.fx_rack.pitch_changed.connect(self.on_pitch_changed)
        self.fx_rack.chance_spin.valueChanged.connect(self._on_chance_changed)
        layout.addWidget(self.fx_rack)

        # Initialize comboboxes
        if self.samples_dict:
            first_cat = sorted(self.samples_dict.keys())[0]
            self.category_combo.setCurrentText(first_cat)
            self.on_category_changed(first_cat)

        # ── Row 3: Step Grid — Scrollable & Responsive ──
        self.grid_scroll = QScrollArea()
        self.grid_scroll.setWidgetResizable(True)
        self.grid_scroll.setFixedHeight(62)
        self.grid_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.grid_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.grid_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        self.grid_container = QWidget()
        self.grid_layout = QHBoxLayout(self.grid_container)
        self.grid_layout.setContentsMargins(0, 4, 0, 4)
        self.grid_layout.setSpacing(3)
        self.grid_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)

        self.grid_scroll.setWidget(self.grid_container)
        layout.addWidget(self.grid_scroll)

        self.rebuild_step_buttons(128)

    def rebuild_step_buttons(self, num_steps: int):
        # Clear existing buttons and separators from grid_layout
        while self.grid_layout.count():
            item = self.grid_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        self.step_buttons = []
        
        # Resize self.steps preserving old values
        old_steps = self.steps
        self.steps = [False] * num_steps
        for i in range(min(len(old_steps), num_steps)):
            self.steps[i] = old_steps[i]

        for i in range(num_steps):
            btn = QPushButton()
            btn.setMinimumSize(28, 36)
            btn.setMaximumWidth(40)
            btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            btn.setObjectName("stepBtn")
            btn.setProperty("active", "true" if self.steps[i] else "false")
            btn.setProperty("playing", "false")
            btn.clicked.connect(lambda checked, idx=i: self.toggle_step(idx))
            self.step_buttons.append(btn)
            self.grid_layout.addWidget(btn)
            
            # Add beat separator every 4 steps
            if (i + 1) % 4 == 0 and i != num_steps - 1:
                sep = QFrame()
                sep.setFixedWidth(2)
                sep.setFixedHeight(24)
                sep.setStyleSheet("background-color: #2a2a3a; border-radius: 1px;")
                self.grid_layout.addWidget(sep)

    def randomize_steps(self):
        import random
        for i in range(len(self.steps)):
            self.steps[i] = random.random() < 0.25
            btn = self.step_buttons[i]
            btn.setProperty("active", "true" if self.steps[i] else "false")
            self.force_style_update(btn)

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

    def _on_mute_toggled(self, checked: bool) -> None:
        if self.track_processor is not None:
            self.track_processor.muted = checked

    def _on_solo_toggled(self, checked: bool) -> None:
        if self.track_processor is not None:
            self.track_processor.soloed = checked

    def _on_chance_changed(self, value: int) -> None:
        self.chance_pct = value

    def on_fx_changed(self, distortion, delay, reverb):
        self.fx_state = (distortion, delay, reverb)
        self._apply_fx_and_pitch_state()

    def on_pitch_changed(self, semitones):
        self.octave_semitones = semitones
        self._apply_fx_and_pitch_state()

    def set_tuning_offset(self, offset_semitones: float) -> None:
        """Applied on top of the per-track pitch control by the workspace's
        global 432/528 Hz tuning toggles, so 'reference pitch' and 'creative
        pitch shift' stay independent controls."""
        self.tuning_offset_semitones = offset_semitones
        self._apply_fx_and_pitch_state()

    def _apply_fx_and_pitch_state(self) -> None:
        """Pushes the FX toggle / pitch / tuning state onto the live
        TrackProcessor. Cheap plain-attribute writes -- unlike the old
        get_or_render_fx() offline path, this never touches disk, so every
        knob tweak is instant instead of triggering a re-render.

        The FX rack is still boolean toggles (Distortion/Delay/Reverb), not
        yet the continuous knobs docs/DAW_VISION_SPEC.md's FX Rack v1
        describes -- that's Phase 4. Until then, "on" maps to the exact
        fixed parameter values the old offline renderer used
        (LEGACY_DISTORTION_DRIVE etc.), so the sound doesn't change out from
        under this pass.
        """
        if self.track_processor is None:
            return
        distortion, delay, reverb = self.fx_state
        state = self.track_processor.state
        state.distortion_drive = LEGACY_DISTORTION_DRIVE if distortion else 0.0
        state.delay_enabled = delay
        state.delay_ms = LEGACY_DELAY_MS
        state.delay_feedback = LEGACY_DELAY_FEEDBACK
        state.delay_mix = LEGACY_DELAY_MIX
        state.reverb_enabled = reverb
        state.reverb_decay = LEGACY_REVERB_DECAY
        state.reverb_mix = LEGACY_REVERB_MIX
        self.track_processor.base_semitones = self.octave_semitones + self.tuning_offset_semitones

    def _ensure_track_processor(self) -> None:
        """Creates this track's TrackProcessor (with a silent placeholder
        sample) the first time it's needed, and registers it with the
        engine. Idempotent -- safe to call before a real sample is chosen."""
        if self.track_processor is not None or self.audio_engine is None:
            return
        self.track_processor = TrackProcessor(self.track_id, TrackState(), _silent_sample())
        self.track_processor.muted = self.btn_mute.isChecked()
        self.track_processor.soloed = self.btn_solo.isChecked()
        self.audio_engine.add_track(self.track_processor)
        self._apply_fx_and_pitch_state()

    def refresh_audio_source(self):
        """Resolves the selected sample to a file path, loads + resamples it
        into the live engine's TrackProcessor. Unlike the old
        get_or_render_fx()-based version, this only runs when the *sample
        selection* changes (category/search/custom pick) -- FX/pitch/tuning
        changes go through _apply_fx_and_pitch_state() instead, since they
        don't need the source audio reloaded from disk."""
        self._ensure_track_processor()
        if not self.current_relative_path or self.track_processor is None:
            return

        custom_prefix = f"{CUSTOM_SAMPLE_CATEGORY}/"
        if self.current_relative_path.startswith(custom_prefix):
            # Custom samples resolve against the open project workspace, not the
            # bundled resources -- see app.services.audio.sample_import and
            # docs/THREAT_MODEL.md's "Custom DAW sample import" entry.
            filename = self.current_relative_path[len(custom_prefix):]
            if not self.workspace_path:
                return
            from app.services.audio.sample_import import resolve_custom_sample_path
            try:
                file_path = resolve_custom_sample_path(self.workspace_path, f"custom/{filename}")
            except SecurityError:
                return
        else:
            try:
                file_path = resolve_sample_path(self.samples_base_dir, self.current_relative_path)
            except SecurityError:
                return

        try:
            sample_data, _rate = load_and_prepare_sample(file_path)
        except Exception:
            # Malformed/corrupt WAV -- see docs/FEATURE_SECURITY_REVIEW.md's
            # "DAW Live Audio Engine" entry: read_wav_stereo doesn't validate
            # WAV structure itself, so this call site is the validation
            # boundary. Fail silently (keep the previous sample playing)
            # rather than crash the whole DAW on one bad file.
            return
        self.track_processor.sample_data = sample_data
        self._apply_fx_and_pitch_state()

    def toggle_step(self, index):
        self.steps[index] = not self.steps[index]
        btn = self.step_buttons[index]
        btn.setProperty("active", "true" if self.steps[index] else "false")
        self.force_style_update(btn)

    def update_playhead(self, step_index):
        """Purely visual -- actual audio triggering happens on the audio
        callback thread in DawWorkspace._on_step_boundary(), not here. This
        is called via a queued Qt signal (see AudioEngineUiBridge), so a few
        milliseconds of Qt event-queue latency here is fine; it would not be
        fine for the actual note trigger."""
        num_buttons = len(self.step_buttons)
        prev = self._last_playhead
        self._last_playhead = step_index

        if 0 <= prev < num_buttons:
            self.step_buttons[prev].setProperty("playing", "false")
            self.force_style_update(self.step_buttons[prev])

        if 0 <= step_index < num_buttons:
            btn = self.step_buttons[step_index]
            btn.setProperty("playing", "true")
            self.force_style_update(btn)
            self.grid_scroll.ensureWidgetVisible(btn)

    def force_style_update(self, widget):
        widget.style().unpolish(widget)
        widget.style().polish(widget)

    def trigger_live(self):
        """Fires the track's currently loaded sample immediately as a held
        note, independent of the step sequencer's playhead. Used for live
        MIDI note-on triggers. Returns the Voice (so the caller can release
        it on MIDI note-off) or None if no engine/processor is available yet."""
        if self.track_processor is None or self.audio_engine is None:
            return None
        return self.audio_engine.note_on(self.track_id)

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

        steps = state.get("steps", [])
        # Pad or truncate steps to match the current step length
        self.steps = [bool(s) for s in steps] + [False] * max(0, len(self.step_buttons) - len(steps))
        self.steps = self.steps[:len(self.step_buttons)]
        for i, active in enumerate(self.steps):
            btn = self.step_buttons[i]
            btn.setProperty("active", "true" if active else "false")
            self.force_style_update(btn)

        self.refresh_audio_source()

class AudioEngineUiBridge(QObject):
    """Marshals the AudioEngine's audio-callback-thread step events onto the
    Qt GUI thread as a queued signal, per the Real-Time Safety Contract's
    "UI updates cross threads via Qt.ConnectionType.QueuedConnection
    signals only" rule.

    Lives in the GUI thread (constructed normally, no moveToThread call), so
    a signal emitted from the audio thread automatically queues onto this
    object's thread instead of running the connected slot inline -- that is
    what makes emit() here safe to call from AudioEngine's callback thread.
    """

    step_advanced = Signal(int)

    def emit_step_advanced(self, step_index: int) -> None:
        self.step_advanced.emit(step_index)


STANDARD_TUNING_HZ = 440.0


class DawWorkspace(QWidget):
    theme_changed = Signal(str)
    asset_dropped = Signal(str)

    def __init__(self):
        super().__init__()
        self.is_playing = False
        self.bpm = 120
        self.tuning_hz = STANDARD_TUNING_HZ
        self.tuning_offset_semitones = 0.0
        self.num_steps = 128
        self.setAcceptDrops(True)

        # Real-time audio engine (docs/DAW_VISION_SPEC.md) -- replaces the
        # old busy-wait AudioEngineThread + paplay-subprocess SamplePlayer.
        self.audio_engine = AudioEngine(block_size=1024)
        self._ui_bridge = AudioEngineUiBridge()
        self._ui_bridge.step_advanced.connect(self._on_step_advanced, Qt.ConnectionType.QueuedConnection)
        self.audio_engine.on_step_boundary = self._on_engine_step_boundary

        self.samples_base_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "resources", "samples")
        self.samples_flat = self.get_available_samples_flat()
        self.samples_dict = self.group_samples(self.samples_flat)
        self.tracks = []

        self.midi_service = None
        self.midi_router = None
        self.workspace_path = None
        # note number -> Voice, for MIDI note-off release. Written/read only
        # from the audio callback thread (see _handle_midi_note_on/_off)
        # since that's where MidiService's callbacks fire from.
        self._active_midi_voices: dict[int, object] = {}

        self.setup_ui()

    def attach_midi_service(self, midi_service, mapper) -> None:
        """Wires a MidiService's note-on/note-off events to live-trigger DAW
        tracks as held notes.

        Routing (which mapper label maps to which track) is handled by a
        MidiTrackRouter so it stays testable without a QApplication; call
        assign_midi_track() afterward to configure at least one mapping.
        Unmapped notes, or mapped labels with no track assigned, are silently
        ignored -- MIDI wiring must never crash playback.
        """
        from app.services.audio.midi_service import MidiTrackRouter

        self.midi_service = midi_service
        self.midi_router = MidiTrackRouter(mapper)
        midi_service.on_note_on(self._handle_midi_note_on)
        midi_service.on_note_off(self._handle_midi_note_off)

    def assign_midi_track(self, label: str, track_index: int) -> None:
        if self.midi_router is None:
            raise SecurityError("Attach a MIDI service before assigning track mappings.")
        self.midi_router.assign(label, track_index, len(self.tracks))

    def _handle_midi_note_on(self, note: int, velocity: int) -> None:
        if self.midi_router is None:
            return
        track_index = self.midi_router.resolve(note, velocity)
        if track_index is not None and 0 <= track_index < len(self.tracks):
            voice = self.tracks[track_index].trigger_live()
            if voice is not None:
                self._active_midi_voices[note] = voice

    def _handle_midi_note_off(self, note: int) -> None:
        voice = self._active_midi_voices.pop(note, None)
        if voice is not None:
            self.audio_engine.note_off_voice(voice)

    def dragEnterEvent(self, event) -> None:  # noqa: N802 - Qt override
        from app.ui.assets.asset_manager import ASSET_MIME_TYPE

        if event.mimeData().hasFormat(ASSET_MIME_TYPE):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802 - Qt override
        from app.ui.assets.asset_manager import ASSET_MIME_TYPE, decode_asset_mime_payload

        mime = event.mimeData()
        if not mime.hasFormat(ASSET_MIME_TYPE):
            return
        try:
            relative_path = decode_asset_mime_payload(bytes(mime.data(ASSET_MIME_TYPE)))
        except SecurityError:
            return
        # Forwarded to MainWindow._route_asset_activation() -- the same
        # validated import path double-clicking an asset already uses.
        self.asset_dropped.emit(relative_path)
        event.acceptProposedAction()

    def set_workspace_path(self, path: str) -> None:
        """Points the DAW at an open project workspace so custom sample import
        (Task F1) becomes available. Pass None to clear it (e.g. no project
        open yet) -- custom samples are hidden again until a workspace returns."""
        self.workspace_path = path
        for track in self.tracks:
            track.workspace_path = path
        self._refresh_custom_samples()

    def _refresh_custom_samples(self) -> None:
        from app.services.audio.sample_import import list_custom_samples

        filenames: list[str] = []
        if self.workspace_path:
            try:
                custom_paths = list_custom_samples(self.workspace_path)
            except SecurityError:
                custom_paths = []
            filenames = [p.split("/", 1)[1] for p in custom_paths if "/" in p]

        if filenames:
            self.samples_dict[CUSTOM_SAMPLE_CATEGORY] = filenames
        else:
            self.samples_dict.pop(CUSTOM_SAMPLE_CATEGORY, None)
        for track in self.tracks:
            if CUSTOM_SAMPLE_CATEGORY in self.samples_dict:
                if track.category_combo.findText(CUSTOM_SAMPLE_CATEGORY) == -1:
                    track.category_combo.addItem(CUSTOM_SAMPLE_CATEGORY)
            else:
                existing_index = track.category_combo.findText(CUSTOM_SAMPLE_CATEGORY)
                if existing_index != -1:
                    track.category_combo.removeItem(existing_index)

    def import_sample_dialog(self) -> None:
        """Lets the user pick a local .wav file and import it into the open
        workspace's audio/custom/ folder (Task F1). Requires a workspace to
        already be open -- there is nowhere safe to copy the sample to
        otherwise, matching the Explorer's "no home-dir indexing until a
        workspace is chosen" discipline."""
        if not self.workspace_path:
            QMessageBox.information(
                self, "Import Sample",
                "Open or save a project workspace first (File > Open Folder / Save Workspace) "
                "before importing your own samples.",
            )
            return
        from PySide6.QtWidgets import QFileDialog
        from app.services.audio.sample_import import import_custom_sample

        file_path, _ = QFileDialog.getOpenFileName(self, "Import Sample", os.path.expanduser("~"), "WAV Audio (*.wav)")
        if not file_path:
            return
        try:
            import_custom_sample(self.workspace_path, file_path)
        except SecurityError as exc:
            QMessageBox.warning(self, "Import Blocked", str(exc))
            return
        self._refresh_custom_samples()

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
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(12)
        
        # ── TRANSPORT BAR — Row 1: Play + BPM ──
        transport_row1 = QHBoxLayout()
        transport_row1.setSpacing(12)
        
        self.play_btn = QPushButton("▶  PLAY")
        self.play_btn.setObjectName("transportPlay")
        self.play_btn.setMinimumSize(90, 36)
        self.play_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.play_btn.setStyleSheet("""
            QPushButton#transportPlay {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #6c5ce7, stop:1 #a855f7);
                border: none; color: white; font-weight: 700; font-size: 13px;
                border-radius: 6px; padding: 0 16px;
            }
            QPushButton#transportPlay:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #7c6cf7, stop:1 #b865ff);
            }
        """)
        self.play_btn.clicked.connect(self.toggle_playback)
        
        bpm_label = QLabel("BPM")
        bpm_label.setStyleSheet("color: #6a6a7a; font-weight: 600; font-size: 11px;")
        self.bpm_slider = QSlider(Qt.Orientation.Horizontal)
        self.bpm_slider.setRange(60, 200)
        self.bpm_slider.setValue(self.bpm)
        self.bpm_slider.setMinimumWidth(120)
        self.bpm_slider.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.bpm_slider.valueChanged.connect(self.update_bpm)
        
        self.bpm_display = QLabel(f"{self.bpm}")
        self.bpm_display.setFixedWidth(32)
        self.bpm_display.setStyleSheet("color: #ffffff; font-weight: 700; font-size: 14px;")

        transport_row1.addWidget(self.play_btn)
        transport_row1.addSpacing(8)
        transport_row1.addWidget(bpm_label)
        transport_row1.addWidget(self.bpm_slider)
        transport_row1.addWidget(self.bpm_display)
        transport_row1.addStretch()

        layout.addLayout(transport_row1)

        # ── TRANSPORT BAR — Row 2: Theme + Tuning + Save/Load/Export ──
        transport_row2 = QHBoxLayout()
        transport_row2.setSpacing(8)

        theme_label = QLabel("Theme")
        theme_label.setStyleSheet("color: #6a6a7a; font-weight: 600; font-size: 11px;")
        self.theme_combo = NoScrollComboBox()
        self.theme_combo.setMinimumWidth(80)
        self.theme_combo.addItems(list(THEME_PRESETS.keys()) + ["Custom"])
        self.theme_combo.currentTextChanged.connect(self.apply_theme)

        tuning_label = QLabel("Tuning")
        tuning_label.setStyleSheet("color: #6a6a7a; font-weight: 600; font-size: 11px;")
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

        self.save_btn = QPushButton("💾 Save")
        self.save_btn.clicked.connect(self.save_pattern_dialog)
        self.load_btn = QPushButton("📂 Load")
        self.load_btn.clicked.connect(self.load_pattern_dialog)
        
        self.export_btn = QPushButton("⬇ Export .wav")
        self.export_btn.setObjectName("exportBtn")
        self.export_btn.clicked.connect(self.export_audio)

        self.import_sample_btn = QPushButton("+ Import Sample")
        self.import_sample_btn.setToolTip(
            "Import a .wav file into this project's custom sample library. "
            "Requires a workspace to be open (File > Open Folder or Save Workspace)."
        )
        self.import_sample_btn.clicked.connect(self.import_sample_dialog)

        transport_row2.addWidget(theme_label)
        transport_row2.addWidget(self.theme_combo)
        transport_row2.addSpacing(8)
        transport_row2.addWidget(tuning_label)
        transport_row2.addWidget(self.btn_tuning_432)
        transport_row2.addWidget(self.btn_tuning_528)
        
        # Steps length selector
        steps_label = QLabel("Length:")
        steps_label.setStyleSheet("color: #6a6a7a; font-weight: 600; font-size: 11px;")
        self.steps_combo = NoScrollComboBox()
        self.steps_combo.addItems(["16 steps", "32 steps", "64 steps", "128 steps"])
        self.steps_combo.setCurrentText("128 steps")
        self.steps_combo.currentTextChanged.connect(self.update_sequence_length)
        transport_row2.addWidget(steps_label)
        transport_row2.addWidget(self.steps_combo)
        
        self.new_btn = QPushButton("✨ New")
        self.new_btn.setToolTip("Clear the workspace and start fresh with a blank slate")
        self.new_btn.clicked.connect(self.clear_workspace)

        transport_row2.addStretch()
        transport_row2.addWidget(self.import_sample_btn)
        transport_row2.addWidget(self.new_btn)
        transport_row2.addWidget(self.save_btn)
        transport_row2.addWidget(self.load_btn)
        transport_row2.addWidget(self.export_btn)

        layout.addLayout(transport_row2)

        # ── Separator ──
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFixedHeight(1)
        sep.setStyleSheet("background-color: #1e1e2e;")
        layout.addWidget(sep)

        # ── TRACKS AREA ──
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QScrollArea.Shape.NoFrame)
        
        self.tracks_container = QWidget()
        self.tracks_layout = QVBoxLayout(self.tracks_container)
        self.tracks_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.tracks_layout.setSpacing(8)
        
        scroll_area.setWidget(self.tracks_container)
        layout.addWidget(scroll_area, 1)
        
        # Add Track Button
        add_track_btn = QPushButton("+ ADD TRACK")
        add_track_btn.setMinimumSize(120, 32)
        add_track_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        add_track_btn.clicked.connect(self.add_track)
        layout.addWidget(add_track_btn, 0, Qt.AlignmentFlag.AlignCenter)
        
        # Try to load 'Techno Banger' preset on startup by default so the user has a working beat immediately
        try:
            from app.storage.database import init_db, load_pattern
            init_db()
            data = load_pattern('House Beat')
            if data:
                self.bpm = data["bpm"]
                self.bpm_slider.setValue(self.bpm)
                self.audio_engine.bpm = self.bpm
                tracks_state = data["tracks"]
                for state in tracks_state:
                    track = DawTrack(self.samples_dict, self.samples_base_dir, self.samples_flat)
                    track.audio_engine = self.audio_engine
                    track.delete_requested.connect(self.remove_track)
                    track.tuning_offset_semitones = self.tuning_offset_semitones
                    self.tracks.append(track)
                    self.tracks_layout.addWidget(track)
                    track.restore_state(state)
            else:
                for _ in range(4):
                    self.add_track()
        except Exception:
            for _ in range(4):
                self.add_track()
            
    def add_track(self):
        track = DawTrack(self.samples_dict, self.samples_base_dir, self.samples_flat)
        track.audio_engine = self.audio_engine
        track.tuning_offset_semitones = self.tuning_offset_semitones
        track.rebuild_step_buttons(self.num_steps)
        track.delete_requested.connect(self.remove_track)
        track._ensure_track_processor()
        self.tracks.append(track)
        self.tracks_layout.addWidget(track)

    def add_track_with_sample(self, relative_path: str) -> None:
        """Adds a new track pre-loaded with relative_path (used by the asset
        manager's "double-click a WAV" wiring).

        relative_path must resolve under the bundled sample library
        (self.samples_base_dir = app/resources/samples) -- the DAW's audio
        pipeline is intentionally scoped there for security (see
        SECURITY.md), so this raises SecurityError for any asset outside that
        root rather than silently failing to play. Loading arbitrary WAVs
        from a user's own asset workspace is a documented future task (it
        needs its own path-validated loading path, not this one).
        """
        resolve_sample_path(self.samples_base_dir, relative_path)
        self.add_track()
        track = self.tracks[-1]
        category, _, filename = relative_path.rpartition("/")
        if category in self.samples_dict and filename in self.samples_dict[category]:
            track.category_combo.setCurrentText(category)
            track.sample_combo.setCurrentText(filename)

    def remove_track(self, track):
        if track in self.tracks:
            self.tracks.remove(track)
            self.tracks_layout.removeWidget(track)
            if track.track_processor is not None:
                self.audio_engine.remove_track(track.track_id)
            track.deleteLater()

    def clear_workspace(self):
        if self.is_playing:
            self.toggle_playback()

        # Clear tracks
        for track in self.tracks:
            self.tracks_layout.removeWidget(track)
            if track.track_processor is not None:
                self.audio_engine.remove_track(track.track_id)
            track.deleteLater()
        self.tracks.clear()

        # Reset BPM
        self.bpm = 120
        self.bpm_slider.setValue(120)
        self.audio_engine.bpm = 120
        self.bpm_display.setText("120")

        # Update display/status
        self.workspace_path = None

        # Add 1 initial blank track so it's not a completely empty box
        self.add_track()

    def update_sequence_length(self, text: str):
        try:
            num_steps = int(text.split()[0])
        except (ValueError, IndexError):
            num_steps = 16
        self.num_steps = num_steps
        for track in self.tracks:
            track.rebuild_step_buttons(num_steps)

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
        self.audio_engine.bpm = self.bpm
        tracks_state = data["tracks"]

        # Set dynamic sequence length from loaded track steps size
        num_steps = len(tracks_state[0].get("steps", [])) if tracks_state else 16
        self.steps_combo.setCurrentText(f"{num_steps} steps")
        
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
        if self.is_playing:
            # Quantized to the next step boundary -- see the Real-Time
            # Safety Contract in docs/DAW_VISION_SPEC.md. Changing BPM
            # mid-step must not retroactively shift the current step.
            self.audio_engine.set_bpm(value)
        else:
            # Not playing -- nothing to quantize against, apply immediately
            # so the displayed/stored bpm and the engine's own bpm never
            # silently disagree while stopped.
            self.audio_engine.bpm = value

    def toggle_playback(self):
        if self.is_playing:
            self.play_btn.setText("▶  PLAY")
            self.play_btn.setStyleSheet("""
                QPushButton#transportPlay {
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #6c5ce7, stop:1 #a855f7);
                    border: none; color: white; font-weight: 700; font-size: 13px;
                    border-radius: 6px; padding: 0 16px;
                }
                QPushButton#transportPlay:hover {
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #7c6cf7, stop:1 #b865ff);
                }
            """)
            self.is_playing = False
            self.audio_engine.stop()
            # Clear playhead
            for track in self.tracks:
                track.update_playhead(-1)
        else:
            self.play_btn.setText("■  STOP")
            self.play_btn.setStyleSheet("""
                QPushButton#transportPlay {
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #e74c3c, stop:1 #c0392b);
                    border: none; color: white; font-weight: 700; font-size: 13px;
                    border-radius: 6px; padding: 0 16px;
                }
                QPushButton#transportPlay:hover {
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #ff5555, stop:1 #e74c3c);
                }
            """)
            self.is_playing = True
            self.audio_engine.bpm = self.bpm
            self.audio_engine.start()

    def _on_engine_step_boundary(self, step_index: int) -> None:
        """Runs on the audio callback thread (AudioEngine.on_step_boundary
        hook) -- must never call a Qt widget method directly, only read
        plain-attribute state and call AudioEngine/TrackProcessor methods.
        See the Real-Time Safety Contract in docs/DAW_VISION_SPEC.md.

        Decides which tracks fire this step (pattern data + the chance
        knob) and triggers them with sample-accurate timing; visual
        feedback is handed off to the GUI thread via the queued bridge
        signal instead of being done here.
        """
        if not self.num_steps:
            return
        wrapped_index = step_index % self.num_steps
        for track in self.tracks:
            if track.track_processor is None:
                continue
            if wrapped_index >= len(track.steps) or not track.steps[wrapped_index]:
                continue
            chance = track.chance_pct
            if chance < 100 and random.random() * 100 > chance:
                continue
            self.audio_engine.trigger_step_note(
                track.track_id, gate_length_pct=track.track_processor.state.gate_length_pct
            )
        self._ui_bridge.emit_step_advanced(wrapped_index)

    def _on_step_advanced(self, step_index: int) -> None:
        """GUI-thread slot, queued off _on_engine_step_boundary -- visual
        playhead update only, no audio triggering here."""
        if not self.is_playing:
            return
        for track in self.tracks:
            track.update_playhead(step_index)

    def get_state(self) -> dict:
        state = {
            "bpm": self.bpm,
            "theme": self.theme_combo.currentText(),
            "tuning_hz": self.tuning_hz,
            "num_steps": self.num_steps,
            "tracks": []
        }
        for track in self.tracks:
            category = track.category_combo.currentText()
            sample = track.sample_combo.currentText()
            track_data = {
                "category": category,
                "sample": sample,
                "steps": track.steps.copy(),
                "chance": track.fx_rack.chance_spin.value(),
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
            
        num_steps = state.get("num_steps", 16)
        self.steps_combo.setCurrentText(f"{num_steps} steps")
            
        if "tracks" in state:
            # Clear existing tracks
            for track in self.tracks:
                self.tracks_layout.removeWidget(track)
                if track.track_processor is not None:
                    self.audio_engine.remove_track(track.track_id)
                track.deleteLater()
            self.tracks.clear()
            
            # Load saved tracks
            for track_data in state["tracks"]:
                track = DawTrack(self.samples_dict, self.samples_base_dir, self.samples_flat)
                track.audio_engine = self.audio_engine
                track._ensure_track_processor()
                track.delete_requested.connect(self.remove_track)
                if track_data.get("category"):
                    track.category_combo.setCurrentText(track_data["category"])
                if track_data.get("sample"):
                    track.sample_combo.setCurrentText(track_data["sample"])
                    
                track.rebuild_step_buttons(self.num_steps)
                    
                if "steps" in track_data:
                    steps_list = track_data["steps"]
                    track.steps = [bool(s) for s in steps_list[:self.num_steps]] + [False] * max(0, self.num_steps - len(steps_list))
                    for i, active in enumerate(track.steps):
                        if active:
                            btn = track.step_buttons[i]
                            btn.setProperty("active", "true")
                            track.force_style_update(btn)
                            
                if "chance" in track_data:
                    track.fx_rack.chance_spin.setValue(track_data["chance"])
                            
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
