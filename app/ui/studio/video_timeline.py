# ==============================================================================
# Program: app/ui/studio/video_timeline.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: the video editor mode. TimelineModel (plain Python, no Qt) holds tracks, clips and the
# playhead and implements the editing rules (placement, overlap prevention, split, move, track
# add/remove, save/load). TimelineWidget draws the model with QPainter and handles mouse and
# wheel interaction. VideoEditorWidget adds a toolbar (add track, import clip) and accepts clips
# dragged from the asset manager.
# Used by: app/application.py (build_video_editor_mode; asset_dropped is routed to the same import
# path as double-clicking an asset); project save/load via get_state/load_state;
# tests/test_video_timeline.py exercises the model without a QApplication.
# Time units: seconds on the model; the widget converts with seconds_per_pixel (zoom).
# Scope: NO video is decoded or played. Clips are placeholders with a source path and a duration
# (imported clips default to 5 s) until real media probing exists.
# Known limits / bugs noticed: load_state does not re-validate saved clips (track indexes,
# overlaps, clip count or that track.index matches its position), so a hand-edited project could
# raise IndexError later; this model allows up to 32 tracks while the project file format
# (app/project.py) allows 16, so a timeline with 17 or more tracks cannot be saved.
# ==============================================================================

"""Non-linear video editing timeline: pure data model plus a QPainter-based widget.

Editing logic (clip placement, overlap prevention, splitting, serialization) lives
in TimelineModel, a plain-Python class with no Qt dependency. TimelineWidget wraps
a TimelineModel and only adds rendering and mouse/wheel interaction on top of it,
so the model can be unit-tested without a QApplication.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPen, QWheelEvent
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.security import SecurityError

# Limits keep a damaged or hostile project from creating unbounded work: at most 32 tracks and 2000 clips.
MAX_TRACKS = 32
MAX_CLIPS_PER_TIMELINE = 2000
DEFAULT_TRACK_COUNT = 4
# Layout, in pixels: track lane height, gap between lanes, and the width of the track-name gutter on the left. Clips start to the right of the gutter.
TRACK_HEIGHT = 60
TRACK_GAP = 4
TRACK_HEADER_WIDTH = 120
# Zoom range expressed as seconds per pixel (smaller = more zoomed in). Default 0.05 s/px means 20 px per second.
MIN_SECONDS_PER_PIXEL = 0.001
MAX_SECONDS_PER_PIXEL = 10.0
DEFAULT_SECONDS_PER_PIXEL = 0.05
# Placeholder length (seconds) for imported clips because the file is not probed.
DEFAULT_IMPORTED_CLIP_DURATION = 5.0

# Accent colours cycled by track index (track 0 green, 1 cyan, ...).
TRACK_COLORS = ("#3ddc97", "#00f0ff", "#ff5f1f", "#a26bfa", "#ff007f", "#ffd23f")


# One clip: which source file, where it sits (track_index and start_time in seconds), how long it plays, and which part of the source it shows (in_point/out_point, seconds into the source; out_point 0 means "not set").
@dataclass
class TimelineClip:
    """A single clip placed on a timeline track."""

    clip_id: int
    source_path: str
    track_index: int
    start_time: float
    duration: float
    in_point: float = 0.0
    out_point: float = 0.0

    def to_dict(self) -> dict:
        """Serialise to a JSON-friendly dict."""
        return {
            "clip_id": self.clip_id,
            "source_path": self.source_path,
            "track_index": self.track_index,
            "start_time": self.start_time,
            "duration": self.duration,
            "in_point": self.in_point,
            "out_point": self.out_point,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TimelineClip":
        """Rebuild a clip from to_dict output; in_point/out_point default to 0.0 for older saves.

        Raises:
            KeyError / ValueError / TypeError: required keys are missing or have the wrong type.
        """
        return cls(
            clip_id=int(data["clip_id"]),
            source_path=str(data["source_path"]),
            track_index=int(data["track_index"]),
            start_time=float(data["start_time"]),
            duration=float(data["duration"]),
            in_point=float(data.get("in_point", 0.0)),
            out_point=float(data.get("out_point", 0.0)),
        )


@dataclass
class TimelineTrack:
    """A horizontal lane of non-overlapping clips."""

    index: int
    name: str = ""
    clips: list[TimelineClip] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Give unnamed tracks a default "Track N" name (1-based)."""
        if not self.name:
            self.name = f"Track {self.index + 1}"

    def to_dict(self) -> dict:
        """Serialise the track and its clips."""
        return {
            "index": self.index,
            "name": self.name,
            "clips": [clip.to_dict() for clip in self.clips],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TimelineTrack":
        """Rebuild a track and its clips from saved state."""
        track = cls(index=int(data["index"]), name=str(data.get("name", "")))
        track.clips = [TimelineClip.from_dict(c) for c in data.get("clips", [])]
        return track


class TimelineModel:
    """Pure-Python timeline data model: tracks, clips, and playhead state."""

    def __init__(self, track_count: int = DEFAULT_TRACK_COUNT) -> None:
        """Create a model with `track_count` empty tracks.

        Raises:
            SecurityError: track_count not in 1..MAX_TRACKS.
        """
        if not 0 < track_count <= MAX_TRACKS:
            raise SecurityError("Track count is out of range.")
        self.tracks: list[TimelineTrack] = [TimelineTrack(index=i) for i in range(track_count)]
        # Clip ids are unique integers handed out by allocate_clip_id and kept above any id seen on load.
        self._next_clip_id = 0
        self.playhead_time = 0.0
        self.seconds_per_pixel = DEFAULT_SECONDS_PER_PIXEL

    def total_clip_count(self) -> int:
        """Return how many clips exist across all tracks."""
        return sum(len(track.clips) for track in self.tracks)

    def allocate_clip_id(self) -> int:
        """Return a fresh clip id and advance the counter."""
        new_id = self._next_clip_id
        self._next_clip_id += 1
        return new_id

    def _clip_overlaps(self, clip: TimelineClip, ignore_id: Optional[int] = None) -> bool:
        """Return True if `clip` would overlap another clip on its track.

        Args:
            clip: candidate clip (its track_index must be valid).
            ignore_id: a clip id to skip, used when moving a clip so it does not collide with itself.
        """
        track = self.tracks[clip.track_index]
        end = clip.start_time + clip.duration
        for existing in track.clips:
            if existing.clip_id == ignore_id:
                continue
            existing_end = existing.start_time + existing.duration
            # Standard interval-overlap test with half-open intervals: clips that merely touch (one ends exactly where the next starts) do not overlap.
            if clip.start_time < existing_end and existing.start_time < end:
                return True
        return False

    def add_clip(self, clip: TimelineClip) -> None:
        """Place a clip on its track.

        Raises:
            SecurityError: track index out of range, duration not positive, too many clips, or overlap on that track.
        """
        if not (0 <= clip.track_index < len(self.tracks)):
            raise SecurityError("Clip track index is out of range.")
        if clip.duration <= 0:
            raise SecurityError("Clip duration must be positive.")
        if self.total_clip_count() >= MAX_CLIPS_PER_TIMELINE:
            raise SecurityError("Timeline has too many clips.")
        if self._clip_overlaps(clip):
            raise SecurityError("Clip overlaps an existing clip on that track.")
        self.tracks[clip.track_index].clips.append(clip)
        # Keep the id counter above explicitly supplied ids so allocate_clip_id never repeats one.
        self._next_clip_id = max(self._next_clip_id, clip.clip_id + 1)

    def find_clip(self, clip_id: int) -> Optional[TimelineClip]:
        """Return the clip with this id, or None."""
        for track in self.tracks:
            for clip in track.clips:
                if clip.clip_id == clip_id:
                    return clip
        return None

    def remove_clip(self, clip_id: int) -> bool:
        """Delete a clip by id; returns False if it does not exist."""
        for track in self.tracks:
            for clip in track.clips:
                if clip.clip_id == clip_id:
                    track.clips.remove(clip)
                    return True
        return False

    def move_clip(self, clip_id: int, track_index: int, start_time: float) -> bool:
        """Move a clip to another track/start time if the destination is free.

        Args:
            clip_id: clip to move.
            track_index: destination track.
            start_time: new start in seconds (negative values are clamped to 0).

        Returns:
            True if moved; False if the clip is unknown or the spot is occupied (nothing changes).

        Raises:
            SecurityError: destination track index out of range.
        """
        clip = self.find_clip(clip_id)
        if clip is None:
            return False
        if not (0 <= track_index < len(self.tracks)):
            raise SecurityError("Target track index is out of range.")
        # Test the new position on a copy; the real clip is only touched if the move is allowed.
        candidate = TimelineClip(
            clip_id=clip.clip_id,
            source_path=clip.source_path,
            track_index=track_index,
            start_time=max(0.0, start_time),
            duration=clip.duration,
            in_point=clip.in_point,
            out_point=clip.out_point,
        )
        if self._clip_overlaps(candidate, ignore_id=clip_id):
            return False
        old_track = self.tracks[clip.track_index]
        # Replace the old clip object with the moved copy (same id), so references to the old object become stale; callers should look clips up by id.
        old_track.clips.remove(clip)
        candidate_track = self.tracks[track_index]
        candidate_track.clips.append(candidate)
        return True

    def split_clip(self, clip_id: int, at_time: float) -> Optional[int]:
        """Cut a clip in two at an absolute timeline time.

        Returns:
            The id of the new right-hand clip, or None if the clip is unknown or `at_time` is not strictly inside it.
        """
        clip = self.find_clip(clip_id)
        if clip is None:
            return None
        if not (clip.start_time < at_time < clip.start_time + clip.duration):
            return None
        first_duration = at_time - clip.start_time
        second_duration = clip.duration - first_duration
        # The right part continues the source where the left part stops, so playback stays seamless.
        second_in_point = clip.in_point + first_duration
        new_clip = TimelineClip(
            clip_id=self.allocate_clip_id(),
            source_path=clip.source_path,
            track_index=clip.track_index,
            start_time=at_time,
            duration=second_duration,
            in_point=second_in_point,
            out_point=clip.out_point,
        )
        # The original clip becomes the left part.
        clip.duration = first_duration
        self.tracks[clip.track_index].clips.append(new_clip)
        return new_clip.clip_id

    def add_track(self, name: str = "") -> int:
        """Append a track and return its index.

        Raises:
            SecurityError: already MAX_TRACKS tracks.
        """
        if len(self.tracks) >= MAX_TRACKS:
            raise SecurityError("Too many tracks.")
        index = len(self.tracks)
        self.tracks.append(TimelineTrack(index=index, name=name))
        return index

    def remove_track(self, index: int) -> bool:
        """Delete a track (and its clips) and renumber the following tracks; returns False for a bad index."""
        if not (0 <= index < len(self.tracks)):
            return False
        del self.tracks[index]
        # Clips store their track index, so they must be updated together with the track list.
        for new_index, track in enumerate(self.tracks):
            track.index = new_index
            for clip in track.clips:
                clip.track_index = new_index
        return True

    def get_state(self) -> dict:
        """Return playhead, zoom and all tracks as a JSON-friendly dict."""
        return {
            "playhead_time": self.playhead_time,
            "seconds_per_pixel": self.seconds_per_pixel,
            "tracks": [track.to_dict() for track in self.tracks],
        }

    def load_state(self, state: dict) -> None:
        """Replace the model's contents from saved state.

        Raises:
            SecurityError: more than MAX_TRACKS tracks.
            KeyError / ValueError / TypeError: malformed track or clip entries (not converted).
        """
        tracks_data = state.get("tracks", [])
        if len(tracks_data) > MAX_TRACKS:
            raise SecurityError("Too many tracks in saved state.")
        self.tracks = [TimelineTrack.from_dict(t) for t in tracks_data]
        # Next clip id continues above the highest saved id.
        max_id = -1
        for track in self.tracks:
            for clip in track.clips:
                max_id = max(max_id, clip.clip_id)
        self._next_clip_id = max_id + 1
        self.playhead_time = float(state.get("playhead_time", 0.0))
        seconds_per_pixel = float(state.get("seconds_per_pixel", DEFAULT_SECONDS_PER_PIXEL))
        # Clamp zoom to its allowed range in case the file holds a nonsense value.
        self.seconds_per_pixel = min(MAX_SECONDS_PER_PIXEL, max(MIN_SECONDS_PER_PIXEL, seconds_per_pixel))


# Draws a TimelineModel and lets the user scrub the playhead, zoom with the wheel, select and drag clips and use a right-click menu.
#
# Signals:
#     clip_selected(int): id of the clip the user clicked.
#     playhead_moved(float): playhead time in seconds after scrubbing.
class TimelineWidget(QWidget):
    """Renders a TimelineModel and handles playhead scrub, zoom, and clip drag."""

    clip_selected = Signal(int)
    playhead_moved = Signal(float)

    def __init__(self, parent: QWidget | None = None, track_count: int = DEFAULT_TRACK_COUNT) -> None:
        """Create the widget and its model.

        Args:
            parent: optional Qt parent.
            track_count: initial number of tracks.
        """
        super().__init__(parent)
        self.setObjectName("TimelineWidget")
        # Tall enough for all lanes plus gaps.
        self.setMinimumHeight(TRACK_HEIGHT * track_count + TRACK_GAP * (track_count + 1))
        # Receive move events without a pressed button.
        self.setMouseTracking(True)
        self.model = TimelineModel(track_count=track_count)
        # Interaction state: selected clip, clip being dragged, and the grab offset (seconds) so a dragged clip does not jump to the cursor.
        self._selected_clip_id: Optional[int] = None
        self._dragging_clip_id: Optional[int] = None
        self._drag_start_time_offset = 0.0
        self._scrubbing = False

    # Thin wrappers that call the model and then repaint.
    # --- Delegated data operations -------------------------------------------------
    def add_clip(self, clip: TimelineClip) -> None:
        """Add a clip to the model (raises SecurityError like the model does) and repaint."""
        self.model.add_clip(clip)
        self.update()

    def remove_clip(self, clip_id: int) -> bool:
        """Remove a clip, drop the selection if it was selected, repaint. Returns True if something was removed."""
        removed = self.model.remove_clip(clip_id)
        if removed:
            if self._selected_clip_id == clip_id:
                self._selected_clip_id = None
            self.update()
        return removed

    def split_clip(self, clip_id: int, at_time: float) -> Optional[int]:
        """Split a clip at a time and repaint; returns the new clip id or None."""
        new_id = self.model.split_clip(clip_id, at_time)
        if new_id is not None:
            self.update()
        return new_id

    def get_state(self) -> dict:
        """Return the model state for project saving."""
        return self.model.get_state()

    def load_state(self, state: dict) -> None:
        """Load saved state, clear the selection and repaint."""
        self.model.load_state(state)
        self._selected_clip_id = None
        self.update()

    # Time <-> pixel conversions, hit testing.
    # --- Coordinate helpers ----------------------------------------------------
    def _time_to_x(self, time_s: float) -> float:
        """x pixel for a time: gutter width plus time divided by seconds_per_pixel."""
        return TRACK_HEADER_WIDTH + time_s / self.model.seconds_per_pixel

    def _x_to_time(self, x: float) -> float:
        """Time at an x pixel (never negative)."""
        return max(0.0, (x - TRACK_HEADER_WIDTH) * self.model.seconds_per_pixel)

    def _track_at_y(self, y: float) -> Optional[int]:
        """Return the index of the track lane at pixel y, or None for the gaps."""
        for track in self.model.tracks:
            top = TRACK_GAP + track.index * (TRACK_HEIGHT + TRACK_GAP)
            if top <= y <= top + TRACK_HEIGHT:
                return track.index
        return None

    def _clip_at_pos(self, x: float, y: float) -> Optional[TimelineClip]:
        """Return the clip under the pixel position, or None."""
        track_index = self._track_at_y(y)
        if track_index is None:
            return None
        time_s = self._x_to_time(x)
        for clip in self.model.tracks[track_index].clips:
            if clip.start_time <= time_s <= clip.start_time + clip.duration:
                return clip
        return None

    # Painting of lanes, clips and the playhead.
    # --- Rendering ---------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802 - Qt override
        """Draw lanes, track names, clips and the playhead.

        Args:
            event: Qt paint event (the whole widget is repainted).
        """
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#1e1e1e"))

        for track in self.model.tracks:
            top = TRACK_GAP + track.index * (TRACK_HEIGHT + TRACK_GAP)
            painter.fillRect(0, int(top), self.width(), TRACK_HEIGHT, QColor("#252526"))

            # The header is drawn after the lane so the name always sits on its own darker gutter.
            # Track header: name label in its own gutter, separate from the scrolling clip area.
            painter.fillRect(0, int(top), TRACK_HEADER_WIDTH, TRACK_HEIGHT, QColor("#151515"))
            painter.setPen(QPen(QColor("#a9b7c6")))
            painter.drawText(8, int(top) + TRACK_HEIGHT // 2 + 4, track.name)

            color = QColor(TRACK_COLORS[track.index % len(TRACK_COLORS)])
            for clip in track.clips:
                x = self._time_to_x(clip.start_time)
                # Width in pixels = duration / seconds_per_pixel (subtracting the gutter offset added by _time_to_x), at least 2 px so tiny clips stay visible.
                w = max(2.0, self._time_to_x(clip.duration) - TRACK_HEADER_WIDTH)
                # The selected clip is drawn 40 percent lighter.
                rect_color = color.lighter(140) if clip.clip_id == self._selected_clip_id else color
                painter.fillRect(int(x), int(top) + 2, int(w), TRACK_HEIGHT - 4, rect_color)
                painter.setPen(QPen(QColor("#000000")))
                painter.drawRect(int(x), int(top) + 2, int(w), TRACK_HEIGHT - 4)

        # Pink vertical line across all tracks.
        playhead_x = self._time_to_x(self.model.playhead_time)
        painter.setPen(QPen(QColor("#ff007f"), 2))
        painter.drawLine(QPointF(playhead_x, 0), QPointF(playhead_x, self.height()))
        painter.end()

    # Mouse and wheel handlers.
    # --- Interaction ---------------------------------------------------------
    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802 - Qt override
        """Zoom: scroll up shrinks seconds-per-pixel by 10 percent (zoom in), scroll down grows it; clamped to the allowed range."""
        delta = event.angleDelta().y()
        factor = 0.9 if delta > 0 else 1.1
        new_scale = self.model.seconds_per_pixel * factor
        self.model.seconds_per_pixel = min(MAX_SECONDS_PER_PIXEL, max(MIN_SECONDS_PER_PIXEL, new_scale))
        self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt override
        """Left click on a clip selects it and starts a drag; left click elsewhere clears the selection and starts scrubbing the playhead. Clicks in the name gutter are ignored."""
        pos = event.position()
        if pos.x() < TRACK_HEADER_WIDTH:
            return
        if event.button() == Qt.MouseButton.LeftButton:
            clip = self._clip_at_pos(pos.x(), pos.y())
            if clip is not None:
                self._selected_clip_id = clip.clip_id
                self._dragging_clip_id = clip.clip_id
                # Remember where inside the clip the user grabbed it.
                self._drag_start_time_offset = self._x_to_time(pos.x()) - clip.start_time
                self.clip_selected.emit(clip.clip_id)
            else:
                self._selected_clip_id = None
                self._scrubbing = True
                self.model.playhead_time = self._x_to_time(pos.x())
                self.playhead_moved.emit(self.model.playhead_time)
            self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt override
        """Drag the selected clip (it may change track; overlaps are refused silently by the model) or scrub the playhead."""
        pos = event.position()
        if self._dragging_clip_id is not None:
            clip = self.model.find_clip(self._dragging_clip_id)
            if clip is not None:
                track_index = self._track_at_y(pos.y())
                if track_index is None:
                    track_index = clip.track_index
                new_start = max(0.0, self._x_to_time(pos.x()) - self._drag_start_time_offset)
                self.model.move_clip(self._dragging_clip_id, track_index, new_start)
                self.update()
        elif self._scrubbing:
            self.model.playhead_time = self._x_to_time(pos.x())
            self.playhead_moved.emit(self.model.playhead_time)
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt override
        """End any drag or scrub."""
        self._dragging_clip_id = None
        self._scrubbing = False

    def contextMenuEvent(self, event) -> None:  # noqa: N802 - Qt override
        """Right-click on a clip offers "Split at Playhead" and "Delete"."""
        pos = event.pos()
        clip = self._clip_at_pos(pos.x(), pos.y())
        if clip is None:
            return
        # Imported here because the menu is only needed on right-click.
        from PySide6.QtWidgets import QMenu

        menu = QMenu(self)
        split_action = menu.addAction("Split at Playhead")
        delete_action = menu.addAction("Delete")
        chosen = menu.exec(event.globalPos())
        if chosen is delete_action:
            self.remove_clip(clip.clip_id)
        elif chosen is split_action:
            self.split_clip(clip.clip_id, self.model.playhead_time)


# Whole video editor screen: toolbar above a TimelineWidget; accepts assets dragged from the asset manager.
#
# Signals:
#     asset_dropped(str): relative path of a dropped asset; the main window decides how to import it.
class VideoEditorWidget(QWidget):
    """Complete video-editor surface: a toolbar (add track / import clip) over the timeline."""

    asset_dropped = Signal(str)

    def __init__(self, parent: QWidget | None = None, track_count: int = DEFAULT_TRACK_COUNT) -> None:
        """Build the toolbar and timeline.

        Args:
            parent: optional Qt parent.
            track_count: initial number of tracks.
        """
        super().__init__(parent)
        self.setObjectName("VideoEditorWidget")
        self.timeline = TimelineWidget(track_count=track_count)
        # Needed so dragEnterEvent/dropEvent are called for asset drags.
        self.setAcceptDrops(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(6, 6, 6, 6)
        self.add_track_btn = QPushButton("+ Add Track")
        self.add_track_btn.clicked.connect(self._on_add_track)
        self.import_clip_btn = QPushButton("Import Clip…")
        self.import_clip_btn.clicked.connect(self._on_import_clip_dialog)
        toolbar.addWidget(self.add_track_btn)
        toolbar.addWidget(self.import_clip_btn)
        toolbar.addStretch()
        layout.addLayout(toolbar)
        layout.addWidget(self.timeline, 1)

    def dragEnterEvent(self, event) -> None:  # noqa: N802 - Qt override
        """Accept only drags carrying the asset manager's custom MIME type."""
        # Imported lazily to avoid a circular import between the studio and asset modules.
        from app.ui.assets.asset_manager import ASSET_MIME_TYPE

        if event.mimeData().hasFormat(ASSET_MIME_TYPE):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802 - Qt override
        """Decode the dropped asset path and emit asset_dropped.

        Invalid payloads are ignored: decode_asset_mime_payload raises SecurityError for empty or non-UTF-8 data.
        """
        from app.ui.assets.asset_manager import ASSET_MIME_TYPE, decode_asset_mime_payload

        mime = event.mimeData()
        if not mime.hasFormat(ASSET_MIME_TYPE):
            return
        try:
            relative_path = decode_asset_mime_payload(bytes(mime.data(ASSET_MIME_TYPE)))
        except SecurityError:
            return
        # The window validates the path again, so this widget never opens files itself.
        # Forwarded to MainWindow._route_asset_activation() -- the same
        # validated import path double-clicking an asset already uses.
        self.asset_dropped.emit(relative_path)
        event.acceptProposedAction()

    def _on_add_track(self) -> None:
        """Add a track, grow the minimum height and repaint; shows a warning if the track limit is reached."""
        try:
            self.timeline.model.add_track()
        except SecurityError as exc:
            QMessageBox.warning(self, "Add Track Blocked", str(exc))
            return
        track_count = len(self.timeline.model.tracks)
        self.timeline.setMinimumHeight(TRACK_HEIGHT * track_count + TRACK_GAP * (track_count + 1))
        self.timeline.update()

    def _on_import_clip_dialog(self) -> None:
        """Ask for a video file (.mp4/.mov filter, "All Files" also offered) and import it at the playhead."""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Import Clip", "", "Video Files (*.mp4 *.mov);;All Files (*)"
        )
        # An empty path means the user cancelled.
        if file_path:
            self.import_clip(file_path)

    def import_clip(self, source_path: str, track_index: int = 0, duration: float = DEFAULT_IMPORTED_CLIP_DURATION) -> None:
        """Adds a clip referencing source_path at the current playhead.

        No real video decoding happens here (per spec); duration is a
        placeholder until real media probing is implemented. Used both by the
        toolbar's file dialog and by the asset-manager drag/double-click wiring.
        """
        # Fall back to track 0 for an invalid index.
        if not (0 <= track_index < len(self.timeline.model.tracks)):
            track_index = 0
        clip = TimelineClip(
            clip_id=self.timeline.model.allocate_clip_id(),
            source_path=source_path,
            track_index=track_index,
            # New clips are placed at the playhead.
            start_time=self.timeline.model.playhead_time,
            duration=duration,
        )
        try:
            self.timeline.add_clip(clip)
        # Overlap, limits etc. are shown to the user instead of crashing.
        except SecurityError as exc:
            QMessageBox.warning(self, "Import Blocked", str(exc))

    def get_state(self) -> dict:
        """Return the timeline state for project saving."""
        return self.timeline.get_state()

    def load_state(self, state: dict) -> None:
        """Restore the timeline from saved state."""
        self.timeline.load_state(state)
