"""
Tests for server.py helper functions.
Priority 5: Pure functions at module scope.
"""

import pytest
from pathlib import Path

from synclab.app.server import (
    _compute_confidence_badge,
    _serialize_timeline,
    _serialize_value,
)


# ---------------------------------------------------------------------------
# _compute_confidence_badge
# ---------------------------------------------------------------------------

class TestComputeConfidenceBadge:
    """Test confidence badge color assignment."""

    def test_high_confidence(self):
        """High confidence (conf >= 0.30, peak_ratio >= 2.0) -> 'high'."""
        item = {"confidence": 0.45, "peak_ratio": 8.0, "method": "xcorr_envelope"}
        assert _compute_confidence_badge(item) == "high"

    def test_high_requires_both_conditions(self):
        """High needs BOTH conf >= 0.30 AND peak_ratio >= 2.0."""
        # High conf but low peak_ratio
        item = {"confidence": 0.45, "peak_ratio": 1.5, "method": "xcorr"}
        assert _compute_confidence_badge(item) != "high"

        # High peak_ratio but low conf
        item = {"confidence": 0.15, "peak_ratio": 8.0, "method": "xcorr"}
        assert _compute_confidence_badge(item) != "high"

    def test_medium_confidence(self):
        """Medium: conf >= 0.10 -> 'medium'."""
        item = {"confidence": 0.15, "peak_ratio": 1.0, "method": "xcorr"}
        assert _compute_confidence_badge(item) == "medium"

    def test_medium_with_low_conf_high_pr(self):
        """Medium: conf >= 0.05 AND peak_ratio >= 1.5 -> 'medium'."""
        item = {"confidence": 0.06, "peak_ratio": 2.0, "method": "xcorr"}
        assert _compute_confidence_badge(item) == "medium"

    def test_low_confidence(self):
        """Very low confidence -> 'low'."""
        item = {"confidence": 0.01, "peak_ratio": 0.5, "method": "xcorr"}
        assert _compute_confidence_badge(item) == "low"

    def test_timestamp_only_always_low(self):
        """timestamp_only method is always 'low' regardless of confidence."""
        item = {"confidence": 0.99, "peak_ratio": 10.0, "method": "timestamp_only"}
        assert _compute_confidence_badge(item) == "low"

    def test_timestamp_fallback_always_low(self):
        """timestamp_fallback is always 'low'."""
        item = {"confidence": 0.50, "peak_ratio": 5.0, "method": "timestamp_fallback"}
        assert _compute_confidence_badge(item) == "low"

    def test_vad_skip_always_low(self):
        """vad_skip is always 'low'."""
        item = {"confidence": 0.0, "method": "vad_skip"}
        assert _compute_confidence_badge(item) == "low"

    def test_missing_keys_default_to_low(self):
        """Empty item should default to 'low'."""
        assert _compute_confidence_badge({}) == "low"

    def test_none_peak_ratio(self):
        """None peak_ratio should be treated as 0."""
        item = {"confidence": 0.35, "peak_ratio": None, "method": "xcorr"}
        # conf >= 0.30 but peak_ratio is None (treated as 0) -> not "high"
        assert _compute_confidence_badge(item) != "high"


# ---------------------------------------------------------------------------
# _serialize_value
# ---------------------------------------------------------------------------

class TestSerializeValue:
    """Test recursive value serialization."""

    def test_path_to_string(self):
        """Path objects should be converted to strings."""
        p = Path("/foo/bar")
        assert _serialize_value(p) == str(p)

    def test_dict_with_paths(self):
        """Paths inside dicts should be converted."""
        p = Path("/a/b")
        result = _serialize_value({"path": p, "name": "test"})
        assert result == {"path": str(p), "name": "test"}

    def test_list_with_paths(self):
        """Paths inside lists should be converted."""
        p1, p2 = Path("/a"), Path("/b")
        result = _serialize_value([p1, p2])
        assert result == [str(p1), str(p2)]

    def test_nested_structure(self):
        """Deep nested structures should be recursively converted."""
        px, py = Path("/x"), Path("/y")
        data = {"items": [{"path": px}, {"path": py}]}
        result = _serialize_value(data)
        assert result == {"items": [{"path": str(px)}, {"path": str(py)}]}

    def test_plain_value_unchanged(self):
        """Non-Path values should be returned as-is."""
        assert _serialize_value(42) == 42
        assert _serialize_value("hello") == "hello"
        assert _serialize_value(3.14) == 3.14
        assert _serialize_value(None) is None


# ---------------------------------------------------------------------------
# _serialize_timeline
# ---------------------------------------------------------------------------

class TestSerializeTimeline:
    """Test timeline serialization for JSON transport."""

    def test_adds_badge_to_each_item(self):
        """Each item should get a 'badge' key."""
        timeline = [
            {"confidence": 0.5, "peak_ratio": 8.0, "method": "xcorr"},
            {"confidence": 0.01, "method": "timestamp_only"},
        ]
        result = _serialize_timeline(timeline)
        assert len(result) == 2
        assert result[0]["badge"] == "high"
        assert result[1]["badge"] == "low"

    def test_converts_paths_to_strings(self):
        """Path objects in items should be converted to strings."""
        timeline = [
            {
                "confidence": 0.5,
                "peak_ratio": 3.0,
                "method": "xcorr",
                "video_path": Path("/videos/test.mp4"),
            }
        ]
        result = _serialize_timeline(timeline)
        assert isinstance(result[0]["video_path"], str)

    def test_empty_timeline(self):
        """Empty timeline should return empty list."""
        assert _serialize_timeline([]) == []
