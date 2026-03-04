"""
Tests for synclab/config.py
Priority 3: Quick, ensures config integrity.
"""

import pytest

from synclab.config import DEFAULT_CONFIG, get_config


class TestDefaultConfig:
    """Test that DEFAULT_CONFIG has all expected keys."""

    def test_has_audio_analysis_keys(self):
        """All audio analysis parameters must be present."""
        required = [
            "sample_rate", "max_camera_sec", "max_zoom_sec",
            "sync_window_sec", "threshold", "bandpass_low", "bandpass_high",
            "timestamp_tolerance_sec", "timestamp_tolerance_max",
            "min_peak_ratio",
        ]
        for key in required:
            assert key in DEFAULT_CONFIG, f"Missing config key: {key}"

    def test_has_multi_slice_keys(self):
        """Multi-slice parameters must be present."""
        required = ["multi_slice_enabled", "multi_slice_count", "multi_slice_duration"]
        for key in required:
            assert key in DEFAULT_CONFIG, f"Missing config key: {key}"

    def test_has_vad_keys(self):
        """VAD parameters must be present."""
        assert "vad_threshold" in DEFAULT_CONFIG

    def test_has_spectral_whiten(self):
        """Spectral whitening parameter must be present."""
        assert "spectral_whiten" in DEFAULT_CONFIG

    def test_has_file_discovery_keys(self):
        """File discovery parameters must be present."""
        required = ["video_extensions", "audio_extensions", "track_types"]
        for key in required:
            assert key in DEFAULT_CONFIG, f"Missing config key: {key}"

    def test_has_premiere_keys(self):
        """Premiere Pro export parameters must be present."""
        required = ["premiere_fps", "premiere_width", "premiere_height", "premiere_sample_rate"]
        for key in required:
            assert key in DEFAULT_CONFIG, f"Missing config key: {key}"

    def test_sample_rate_is_8000(self):
        """Default sample rate should be 8000 Hz."""
        assert DEFAULT_CONFIG["sample_rate"] == 8000

    def test_min_peak_ratio_is_2(self):
        """Default min_peak_ratio should be 2.0 (v1.2.2)."""
        assert DEFAULT_CONFIG["min_peak_ratio"] == 2.0

    def test_threshold_is_005(self):
        """Default confidence threshold should be 0.05."""
        assert DEFAULT_CONFIG["threshold"] == 0.05

    def test_video_extensions_are_lowercase(self):
        """Video extensions should be lowercase with dots."""
        for ext in DEFAULT_CONFIG["video_extensions"]:
            assert ext.startswith("."), f"Extension missing dot: {ext}"
            assert ext == ext.lower(), f"Extension not lowercase: {ext}"


class TestGetConfig:
    """Test config override functionality."""

    def test_returns_default_without_overrides(self):
        """Without overrides, should return a copy of DEFAULT_CONFIG."""
        config = get_config()
        assert config == DEFAULT_CONFIG
        # Must be a copy, not the same object
        assert config is not DEFAULT_CONFIG

    def test_override_single_key(self):
        """Should override the specified key."""
        config = get_config(sample_rate=16000)
        assert config["sample_rate"] == 16000
        # Other keys unchanged
        assert config["threshold"] == DEFAULT_CONFIG["threshold"]

    def test_override_multiple_keys(self):
        """Should override multiple keys at once."""
        config = get_config(sample_rate=16000, threshold=0.10)
        assert config["sample_rate"] == 16000
        assert config["threshold"] == 0.10

    def test_does_not_mutate_default(self):
        """Overrides must not modify the original DEFAULT_CONFIG."""
        original_sr = DEFAULT_CONFIG["sample_rate"]
        config = get_config(sample_rate=44100)
        assert DEFAULT_CONFIG["sample_rate"] == original_sr
