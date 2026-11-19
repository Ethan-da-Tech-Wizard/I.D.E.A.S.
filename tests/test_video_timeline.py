# ==============================================================================
# Program: tests/test_video_timeline.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: tests the timeline data model of the video editor (app/ui/studio/video_timeline.py):
# TimelineModel/TimelineTrack/TimelineClip. Covers adding, overlap prevention, splitting, moving,
# track management, size limits and state save/load. Times are in seconds.
# Run: python3 -m pytest tests/test_video_timeline.py -q     (needs PySide6)
# ==============================================================================

from __future__ import annotations

import pytest

from app.security import SecurityError
from app.ui.studio.video_timeline import (
    MAX_TRACKS,
    TimelineClip,
    TimelineModel,
    TimelineTrack,
)


# Helper: a 2 s (by default) clip of intro.mp4 on a chosen track and start time.
def make_clip(clip_id: int, track_index: int = 0, start: float = 0.0, duration: float = 2.0) -> TimelineClip:
    """Create a TimelineClip with the given id, track, start and duration."""
    return TimelineClip(
        clip_id=clip_id,
        source_path="clips/intro.mp4",
        track_index=track_index,
        start_time=start,
        duration=duration,
    )


# A clip is stored on its track and counted.
def test_add_clip_happy_path() -> None:
    model = TimelineModel(track_count=2)
    model.add_clip(make_clip(0, start=0.0, duration=3.0))

    assert model.total_clip_count() == 1
    assert model.tracks[0].clips[0].clip_id == 0


# Track 5 does not exist in a 2-track timeline.
def test_add_clip_rejects_track_out_of_range() -> None:
    model = TimelineModel(track_count=2)
    with pytest.raises(SecurityError):
        model.add_clip(make_clip(0, track_index=5))


# Two clips cannot occupy the same time on one track (a track plays one clip at a time); 2.0 to 3.0 overlaps 0.0 to 5.0.
def test_add_clip_prevents_overlap_on_same_track() -> None:
    model = TimelineModel(track_count=1)
    model.add_clip(make_clip(0, start=0.0, duration=5.0))
    with pytest.raises(SecurityError):
        model.add_clip(make_clip(1, start=2.0, duration=1.0))


# Touching ends (clip A ends at 5.0, clip B starts at 5.0) is allowed: intervals are half-open.
def test_add_clip_allows_adjacent_non_overlapping_clips() -> None:
    model = TimelineModel(track_count=1)
    model.add_clip(make_clip(0, start=0.0, duration=5.0))
    model.add_clip(make_clip(1, start=5.0, duration=2.0))
    assert model.total_clip_count() == 2


# Splitting a 10 s clip at 4 s gives a 4 s first part and a 6 s second part that starts at 4 s and
# whose source in_point moves to 4 s, so it keeps playing the same footage where the cut happened.
def test_split_clip_creates_two_clips_with_correct_durations() -> None:
    model = TimelineModel(track_count=1)
    model.add_clip(make_clip(0, start=0.0, duration=10.0))

    new_id = model.split_clip(0, at_time=4.0)

    assert new_id is not None
    first = model.find_clip(0)
    second = model.find_clip(new_id)
    assert first.duration == pytest.approx(4.0)
    assert second.start_time == pytest.approx(4.0)
    assert second.duration == pytest.approx(6.0)
    assert second.in_point == pytest.approx(4.0)


# Cuts outside the clip, or on an unknown clip id, return None (no change).
def test_split_clip_rejects_time_outside_clip_bounds() -> None:
    model = TimelineModel(track_count=1)
    model.add_clip(make_clip(0, start=0.0, duration=10.0))

    assert model.split_clip(0, at_time=20.0) is None
    assert model.split_clip(999, at_time=1.0) is None


# Removal returns True once and False when repeated.
def test_remove_clip() -> None:
    model = TimelineModel(track_count=1)
    model.add_clip(make_clip(0))
    assert model.remove_clip(0) is True
    assert model.total_clip_count() == 0
    assert model.remove_clip(0) is False


# Moving clip 1 onto track 0 at 1.0 s collides with clip 0 (False, unchanged); at 10.0 s it is free (True).
def test_move_clip_rejects_overlap_but_allows_free_slot() -> None:
    model = TimelineModel(track_count=2)
    model.add_clip(make_clip(0, track_index=0, start=0.0, duration=5.0))
    model.add_clip(make_clip(1, track_index=1, start=0.0, duration=5.0))

    assert model.move_clip(1, track_index=0, start_time=1.0) is False
    assert model.move_clip(1, track_index=0, start_time=10.0) is True
    assert model.find_clip(1).track_index == 0
    assert model.find_clip(1).start_time == pytest.approx(10.0)


# Removing track 0 shifts later tracks down, and the clips on them get their track_index updated.
def test_add_track_and_remove_track_reindexes_clips() -> None:
    model = TimelineModel(track_count=2)
    model.add_clip(make_clip(0, track_index=1))

    new_index = model.add_track("FX")
    assert new_index == 2
    assert len(model.tracks) == 3

    assert model.remove_track(0) is True
    assert len(model.tracks) == 2
    # Track that held clip 0 shifted from index 1 -> 0
    assert model.find_clip(0).track_index == 0


# Invalid index: nothing is removed.
def test_remove_track_out_of_range_returns_false() -> None:
    model = TimelineModel(track_count=1)
    assert model.remove_track(5) is False


# A timeline needs at least one track and at most MAX_TRACKS (limits resource use and mirrors the project-file limit).
def test_track_count_bounds() -> None:
    with pytest.raises(SecurityError):
        TimelineModel(track_count=0)
    with pytest.raises(SecurityError):
        TimelineModel(track_count=MAX_TRACKS + 1)


# Tracks, clips, playhead and zoom (seconds_per_pixel) survive get_state/load_state. After loading,
# allocate_clip_id continues above the highest restored id, so new clips never reuse an id.
def test_state_round_trip() -> None:
    model = TimelineModel(track_count=2)
    model.add_clip(make_clip(0, track_index=0, start=0.0, duration=3.0))
    model.add_clip(make_clip(1, track_index=1, start=1.0, duration=2.0))
    model.playhead_time = 1.5
    model.seconds_per_pixel = 0.2

    state = model.get_state()

    restored = TimelineModel(track_count=1)
    restored.load_state(state)

    assert len(restored.tracks) == 2
    assert restored.playhead_time == pytest.approx(1.5)
    assert restored.seconds_per_pixel == pytest.approx(0.2)
    assert restored.find_clip(0).source_path == "clips/intro.mp4"
    # New clips continue from the highest restored clip_id
    new_id = restored.allocate_clip_id()
    assert new_id == 2


# A saved state with more than MAX_TRACKS tracks is refused.
def test_load_state_rejects_too_many_tracks() -> None:
    model = TimelineModel(track_count=1)
    bad_state = {"tracks": [TimelineTrack(index=i).to_dict() for i in range(MAX_TRACKS + 1)]}
    with pytest.raises(SecurityError):
        model.load_state(bad_state)


# Older saves lack in/out points; they default to 0.0 instead of raising KeyError.
def test_timeline_clip_from_dict_defaults_in_out_points() -> None:
    clip = TimelineClip.from_dict(
        {"clip_id": 5, "source_path": "a.mp4", "track_index": 0, "start_time": 0.0, "duration": 1.0}
    )
    assert clip.in_point == 0.0
    assert clip.out_point == 0.0
