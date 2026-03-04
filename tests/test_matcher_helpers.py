"""
Tests for SmartMatcher helper functions (calibration, assignment, validation).
Priority 4: Calibration logic tested with synthetic datetimes (no I/O).

Tests access private methods via instance for characterization before refactoring.
After refactoring (Step 3), imports will change to module-level functions.
"""

import datetime
import pytest
from unittest.mock import MagicMock

from synclab.config import get_config
from synclab.core.matcher import SmartMatcher


@pytest.fixture
def matcher():
    """Create a SmartMatcher with a mock engine."""
    engine = MagicMock()
    config = get_config()
    return SmartMatcher(engine, config)


# ---------------------------------------------------------------------------
# _valid_offset
# ---------------------------------------------------------------------------

class TestValidOffset:
    """Test offset validation logic."""

    def test_normal_offset(self, matcher):
        """A normal offset (video starts 30s into recorder) should be valid."""
        assert matcher._valid_offset(30.0, video_duration=60.0, recorder_duration=600.0)

    def test_offset_at_zero(self, matcher):
        """Offset at the beginning should be valid."""
        assert matcher._valid_offset(0.0, video_duration=60.0, recorder_duration=600.0)

    def test_negative_offset_small(self, matcher):
        """Small negative offsets (up to -30s) should be valid."""
        assert matcher._valid_offset(-10.0, video_duration=60.0, recorder_duration=600.0)

    def test_very_negative_offset(self, matcher):
        """Offset below -30s should be invalid."""
        assert not matcher._valid_offset(-50.0, video_duration=60.0, recorder_duration=600.0)

    def test_offset_beyond_recorder(self, matcher):
        """Offset past the end of the recorder should be invalid."""
        assert not matcher._valid_offset(700.0, video_duration=60.0, recorder_duration=600.0)

    def test_offset_near_end_is_valid(self, matcher):
        """Video starting near end of recorder (but fitting) should be valid."""
        # max_camera_sec=60, so we check offset + 60 <= recorder_duration + 15
        assert matcher._valid_offset(550.0, video_duration=120.0, recorder_duration=600.0)

    def test_zero_duration_always_valid(self, matcher):
        """If durations are 0 or negative, should return True (can't validate)."""
        assert matcher._valid_offset(100.0, video_duration=0, recorder_duration=0)
        assert matcher._valid_offset(100.0, video_duration=-1, recorder_duration=600)


# ---------------------------------------------------------------------------
# _calibrate_clock_offset
# ---------------------------------------------------------------------------

class TestCalibrateClockOffset:
    """Test 3-pass clock offset calibration with synthetic datetimes."""

    def test_exact_offset_recovery(self, matcher):
        """With a known 1-hour offset, calibration should find it."""
        base = datetime.datetime(2026, 1, 15, 14, 0, 0)
        offset_sec = 3600  # 1 hour

        # 5 videos created at 14:00, 14:10, 14:20, 14:30, 14:40
        video_times = [
            base + datetime.timedelta(minutes=i * 10)
            for i in range(5)
        ]

        # Recorders: TIGHT ranges (just 10 seconds around the exact target)
        # so only the correct offset can match all 5
        recorder_times = []
        for i in range(5):
            target = base - datetime.timedelta(seconds=offset_sec) + datetime.timedelta(minutes=i * 10)
            start = target - datetime.timedelta(seconds=5)
            end = target + datetime.timedelta(seconds=5)
            recorder_times.append((start, end))

        result_offset, count = matcher._calibrate_clock_offset(video_times, recorder_times)

        assert count >= 4, f"Expected at least 4 matches, got {count}"
        assert result_offset is not None
        assert abs(result_offset.total_seconds() - offset_sec) < 16.0, (
            f"Expected offset ~{offset_sec}s, got {result_offset.total_seconds():.1f}s"
        )

    def test_negative_offset(self, matcher):
        """Camera clock behind recorder (negative offset) should still be found."""
        base = datetime.datetime(2026, 1, 15, 14, 0, 0)
        offset_sec = -1800  # Camera is 30 min behind

        video_times = [
            base + datetime.timedelta(minutes=i * 10)
            for i in range(3)
        ]

        # Tight ranges so only the correct offset matches
        recorder_times = []
        for i in range(3):
            target = base - datetime.timedelta(seconds=offset_sec) + datetime.timedelta(minutes=i * 10)
            start = target - datetime.timedelta(seconds=5)
            end = target + datetime.timedelta(seconds=5)
            recorder_times.append((start, end))

        result_offset, count = matcher._calibrate_clock_offset(video_times, recorder_times)
        assert count >= 2
        assert result_offset is not None
        assert abs(result_offset.total_seconds() - offset_sec) < 16.0

    def test_no_valid_timestamps(self, matcher):
        """All None timestamps should return (None, 0)."""
        result_offset, count = matcher._calibrate_clock_offset(
            [None, None, None],
            [(None, None), (None, None)]
        )
        assert result_offset is None
        assert count == 0

    def test_empty_lists(self, matcher):
        """Empty lists should return (None, 0)."""
        result_offset, count = matcher._calibrate_clock_offset([], [])
        assert result_offset is None
        assert count == 0


# ---------------------------------------------------------------------------
# _timestamp_assign
# ---------------------------------------------------------------------------

class TestTimestampAssign:
    """Test timestamp-based video-to-recorder assignment."""

    def test_assigns_video_to_correct_recorder(self, matcher):
        """Each video should be assigned to the recorder covering its time."""
        base = datetime.datetime(2026, 1, 15, 14, 0, 0)
        clock_offset = datetime.timedelta(seconds=0)  # No offset

        video_times = [
            base + datetime.timedelta(minutes=5),   # Should match recorder 0
            base + datetime.timedelta(minutes=15),  # Should match recorder 1
            base + datetime.timedelta(minutes=25),  # Should match recorder 2
        ]

        recorder_times = [
            (base, base + datetime.timedelta(minutes=10)),     # 14:00-14:10
            (base + datetime.timedelta(minutes=10),
             base + datetime.timedelta(minutes=20)),           # 14:10-14:20
            (base + datetime.timedelta(minutes=20),
             base + datetime.timedelta(minutes=30)),           # 14:20-14:30
        ]

        assignments = matcher._timestamp_assign(video_times, recorder_times, clock_offset)

        assert 0 in assignments, "Video 0 should be assigned"
        assert 1 in assignments, "Video 1 should be assigned"
        assert 2 in assignments, "Video 2 should be assigned"
        assert assignments[0][0] == 0, "Video 0 should match recorder 0"
        assert assignments[1][0] == 1, "Video 1 should match recorder 1"
        assert assignments[2][0] == 2, "Video 2 should match recorder 2"

    def test_skips_none_timestamps(self, matcher):
        """Videos with None timestamps should not be assigned."""
        base = datetime.datetime(2026, 1, 15, 14, 0, 0)
        clock_offset = datetime.timedelta(seconds=0)

        video_times = [None, base + datetime.timedelta(minutes=5)]
        recorder_times = [(base, base + datetime.timedelta(minutes=10))]

        assignments = matcher._timestamp_assign(video_times, recorder_times, clock_offset)
        assert 0 not in assignments
        assert 1 in assignments

    def test_none_clock_offset_returns_empty(self, matcher):
        """None clock_offset should return empty dict."""
        base = datetime.datetime(2026, 1, 15, 14, 0, 0)
        assignments = matcher._timestamp_assign(
            [base], [(base, base + datetime.timedelta(minutes=10))], None
        )
        assert assignments == {}

    def test_predicted_offset_is_reasonable(self, matcher):
        """The predicted offset within the recorder should be positive."""
        base = datetime.datetime(2026, 1, 15, 14, 0, 0)
        clock_offset = datetime.timedelta(seconds=0)

        video_times = [base + datetime.timedelta(minutes=5)]
        recorder_times = [(base, base + datetime.timedelta(minutes=30))]

        assignments = matcher._timestamp_assign(video_times, recorder_times, clock_offset)
        assert 0 in assignments
        ri, pred_offset = assignments[0]
        assert 200 < pred_offset < 400, (
            f"Expected ~300s offset (5min into recorder), got {pred_offset:.1f}s"
        )

    def test_sanity_check_rejects_beyond_duration(self, matcher):
        """If predicted offset exceeds recorder duration, assignment should be skipped."""
        base = datetime.datetime(2026, 1, 15, 14, 0, 0)
        clock_offset = datetime.timedelta(seconds=0)

        # Video at 14:10, but recorder only covers 14:00-14:05 (5min = 300s)
        # Filesystem times might say 14:00-14:15, but actual audio is only 300s
        video_times = [base + datetime.timedelta(minutes=10)]
        recorder_times = [(base, base + datetime.timedelta(minutes=15))]
        recorder_durations = [300.0]  # Only 5 minutes of actual audio

        assignments = matcher._timestamp_assign(
            video_times, recorder_times, clock_offset,
            recorder_durations=recorder_durations,
        )
        # Predicted offset would be ~600s, but recorder is only 300s
        assert 0 not in assignments, (
            "Should reject assignment where offset exceeds recorder duration"
        )
