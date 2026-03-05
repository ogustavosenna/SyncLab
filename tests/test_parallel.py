"""
Tests for parallel brute-force infrastructure (v1.3.1).

Validates:
  - Thread-safe zoom cache (lock + warm_zoom_cache)
  - Worker function _brute_force_one_video
  - ThreadPoolExecutor integration doesn't regress
"""

import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from synclab.core.engine import SyncEngine
from synclab.core.matcher import _brute_force_one_video


# ---------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------

def _make_engine(config=None):
    """Create a SyncEngine with default test config."""
    cfg = {
        "sample_rate": 8000,
        "max_camera_sec": 60,
        "max_zoom_sec": 0,
        "bandpass_low": 200,
        "bandpass_high": 4000,
        "threshold": 0.05,
        "min_peak_ratio": 2.0,
    }
    if config:
        cfg.update(config)
    return SyncEngine(cfg)


# ---------------------------------------------------------------
# Test: Thread-safe zoom cache
# ---------------------------------------------------------------

class TestZoomCacheLock:
    """Verify _zoom_lock and thread-safe cache operations."""

    def test_engine_has_zoom_lock(self):
        engine = _make_engine()
        assert hasattr(engine, "_zoom_lock")
        assert isinstance(engine._zoom_lock, type(threading.Lock()))

    def test_engine_has_zoom_cache(self):
        engine = _make_engine()
        assert hasattr(engine, "_zoom_cache")
        assert isinstance(engine._zoom_cache, dict)
        assert len(engine._zoom_cache) == 0

    def test_clear_zoom_cache_resets(self):
        engine = _make_engine()
        engine._zoom_cache["fake_key"] = Path("/tmp/fake.wav")
        assert len(engine._zoom_cache) == 1
        engine.clear_zoom_cache()
        assert len(engine._zoom_cache) == 0

    def test_warm_zoom_cache_returns_count(self):
        engine = _make_engine()
        # Mock _get_zoom_audio to return a fake path
        engine._get_zoom_audio = MagicMock(return_value=Path("/tmp/cached.wav"))
        audio_groups = [
            {"wav_files": [Path("/audio/z1.wav"), Path("/audio/z2.wav")]},
            {"wav_files": [Path("/audio/z3.wav")]},
        ]
        count = engine.warm_zoom_cache(audio_groups, Path("/tmp"))
        assert count == 3
        assert engine._get_zoom_audio.call_count == 3

    def test_warm_zoom_cache_skips_failures(self):
        engine = _make_engine()
        call_count = 0
        def mock_get_zoom(wav_path, temp_dir):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                return None  # Simulate failure
            return Path(f"/tmp/cached_{call_count}.wav")

        engine._get_zoom_audio = mock_get_zoom
        audio_groups = [
            {"wav_files": [Path("/a/1.wav"), Path("/a/2.wav"), Path("/a/3.wav")]},
        ]
        count = engine.warm_zoom_cache(audio_groups, Path("/tmp"))
        assert count == 2  # 3 files, 1 failed

    def test_warm_zoom_cache_handles_empty_groups(self):
        engine = _make_engine()
        engine._get_zoom_audio = MagicMock(return_value=Path("/tmp/x.wav"))
        count = engine.warm_zoom_cache([], Path("/tmp"))
        assert count == 0
        engine._get_zoom_audio.assert_not_called()


# ---------------------------------------------------------------
# Test: _brute_force_one_video worker function
# ---------------------------------------------------------------

class TestBruteForceWorker:
    """Verify the module-level worker function returns correct structure."""

    def _make_mock_engine(self, sync_result=None):
        """Create a mock engine with controllable sync_with_zoom."""
        engine = _make_engine()
        if sync_result is None:
            sync_result = {
                "offset": 5.0,
                "confidence": 0.85,
                "method": "stage1_multislice",
                "level": 1,
                "peak_ratio": 8.5,
                "diagnostics": {"stages": [], "speech_ratio": 0.5},
            }

        engine.prepare_camera_audio = MagicMock(
            return_value=Path("/tmp/cam.wav"),
        )
        engine.sync_with_zoom = MagicMock(return_value=dict(sync_result))
        return engine

    def test_returns_matched_dict(self):
        engine = self._make_mock_engine()
        video = {"path": Path("/v/clip01.MP4"), "source_folder": "A"}
        video_info = {"duration": 30.0, "has_audio": True}
        audio_groups = [
            {
                "zoom_dir": Path("/z/ZOOM0001"),
                "wav_files": [Path("/z/ZOOM0001/z.wav")],
                "source_folder": "A",
            },
        ]
        recorder_durations = [120.0]

        result = _brute_force_one_video(
            engine=engine,
            vi=0,
            video=video,
            video_info=video_info,
            audio_groups=audio_groups,
            recorder_durations=recorder_durations,
            temp_dir=Path("/tmp"),
            config={"threshold": 0.05, "min_peak_ratio": 2.0, "max_camera_sec": 60},
            multi_source=False,
        )

        assert result["vi"] == 0
        assert result["matched"] is True
        assert result["ri"] == 0
        assert "result" in result
        assert result["result"]["confidence"] == 0.85
        assert "diagnostics" in result

    def test_returns_no_match_below_threshold(self):
        engine = self._make_mock_engine({
            "offset": 1.0,
            "confidence": 0.01,
            "method": "stage1",
            "level": 1,
            "peak_ratio": 1.5,
            "diagnostics": {"stages": []},
        })

        video = {"path": Path("/v/clip.MP4"), "source_folder": ""}
        video_info = {"duration": 20.0, "has_audio": True}
        audio_groups = [
            {
                "zoom_dir": Path("/z/Z1"),
                "wav_files": [Path("/z/Z1/z.wav")],
                "source_folder": "",
            },
        ]

        result = _brute_force_one_video(
            engine=engine,
            vi=3,
            video=video,
            video_info=video_info,
            audio_groups=audio_groups,
            recorder_durations=[60.0],
            temp_dir=Path("/tmp"),
            config={"threshold": 0.05, "min_peak_ratio": 2.0, "max_camera_sec": 60},
            multi_source=False,
        )

        assert result["vi"] == 3
        assert result["matched"] is False
        assert "diagnostics" in result

    def test_returns_no_match_when_no_camera_audio(self):
        engine = self._make_mock_engine()
        engine.prepare_camera_audio = MagicMock(return_value=None)

        video = {"path": Path("/v/clip.MP4"), "source_folder": ""}
        video_info = {"duration": 10.0, "has_audio": True}
        audio_groups = [
            {
                "zoom_dir": Path("/z/Z1"),
                "wav_files": [Path("/z/Z1/z.wav")],
                "source_folder": "",
            },
        ]

        result = _brute_force_one_video(
            engine=engine,
            vi=5,
            video=video,
            video_info=video_info,
            audio_groups=audio_groups,
            recorder_durations=[60.0],
            temp_dir=Path("/tmp"),
            config={"threshold": 0.05, "min_peak_ratio": 2.0, "max_camera_sec": 60},
            multi_source=False,
        )

        assert result["vi"] == 5
        assert result["matched"] is False
        assert "no_camera_audio" in result["diagnostics"]["method_used"]

    def test_skips_short_recorder(self):
        """Recorder shorter than 50% of video duration should be skipped."""
        engine = self._make_mock_engine()

        video = {"path": Path("/v/clip.MP4"), "source_folder": ""}
        video_info = {"duration": 120.0, "has_audio": True}
        audio_groups = [
            {
                "zoom_dir": Path("/z/Z1"),
                "wav_files": [Path("/z/Z1/z.wav")],
                "source_folder": "",
            },
        ]
        # Recorder is 30s, video is 120s -> 30 < 120*0.5 -> skip
        result = _brute_force_one_video(
            engine=engine,
            vi=0,
            video=video,
            video_info=video_info,
            audio_groups=audio_groups,
            recorder_durations=[30.0],
            temp_dir=Path("/tmp"),
            config={"threshold": 0.05, "min_peak_ratio": 2.0, "max_camera_sec": 60},
            multi_source=False,
        )

        assert result["matched"] is False
        engine.sync_with_zoom.assert_not_called()

    def test_multi_source_preference(self):
        """When multi_source=True, prefer match from same source folder."""
        call_count = 0
        def fake_sync(cam_wav, wav_files, temp_dir, suffix=""):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First group (different source) — high confidence
                return {
                    "offset": 3.0, "confidence": 0.9,
                    "method": "stage1", "level": 1,
                    "peak_ratio": 10.0,
                    "diagnostics": {"stages": []},
                }
            else:
                # Second group (same source) — slightly lower confidence
                return {
                    "offset": 5.0, "confidence": 0.7,
                    "method": "stage2", "level": 2,
                    "peak_ratio": 6.0,
                    "diagnostics": {"stages": []},
                }

        engine = _make_engine()
        engine.prepare_camera_audio = MagicMock(return_value=Path("/tmp/cam.wav"))
        engine.sync_with_zoom = fake_sync

        video = {"path": Path("/v/clip.MP4"), "source_folder": "CardB"}
        video_info = {"duration": 30.0, "has_audio": True}
        audio_groups = [
            {
                "zoom_dir": Path("/z/Z1"),
                "wav_files": [Path("/z/Z1/z.wav")],
                "source_folder": "CardA",
            },
            {
                "zoom_dir": Path("/z/Z2"),
                "wav_files": [Path("/z/Z2/z.wav")],
                "source_folder": "CardB",
            },
        ]

        result = _brute_force_one_video(
            engine=engine,
            vi=0,
            video=video,
            video_info=video_info,
            audio_groups=audio_groups,
            recorder_durations=[120.0, 120.0],
            temp_dir=Path("/tmp"),
            config={"threshold": 0.05, "min_peak_ratio": 2.0, "max_camera_sec": 60},
            multi_source=True,
        )

        assert result["matched"] is True
        # Should prefer ri=1 (same source) even though ri=0 has higher conf
        assert result["ri"] == 1
        assert result["result"]["confidence"] == 0.7

    def test_weak_peak_ratio_rejects(self):
        """Match with peak_ratio below min_peak_ratio should be rejected."""
        engine = self._make_mock_engine({
            "offset": 2.0,
            "confidence": 0.8,
            "method": "stage1",
            "level": 1,
            "peak_ratio": 1.5,  # Below default 2.0
            "diagnostics": {"stages": []},
        })

        video = {"path": Path("/v/clip.MP4"), "source_folder": ""}
        video_info = {"duration": 30.0, "has_audio": True}
        audio_groups = [
            {
                "zoom_dir": Path("/z/Z1"),
                "wav_files": [Path("/z/Z1/z.wav")],
                "source_folder": "",
            },
        ]

        result = _brute_force_one_video(
            engine=engine,
            vi=0,
            video=video,
            video_info=video_info,
            audio_groups=audio_groups,
            recorder_durations=[120.0],
            temp_dir=Path("/tmp"),
            config={"threshold": 0.05, "min_peak_ratio": 2.0, "max_camera_sec": 60},
            multi_source=False,
        )

        assert result["matched"] is False
        assert "weak_peak" in result["diagnostics"]["why_fallback"]


# ---------------------------------------------------------------
# Test: Thread safety
# ---------------------------------------------------------------

class TestThreadSafety:
    """Verify concurrent access to the zoom cache doesn't corrupt state."""

    def test_concurrent_cache_writes(self):
        """Multiple threads writing to zoom cache shouldn't corrupt it."""
        engine = _make_engine()
        results = []
        errors = []

        def worker(thread_id):
            try:
                for i in range(50):
                    key = f"thread_{thread_id}_file_{i}"
                    with engine._zoom_lock:
                        engine._zoom_cache[key] = Path(f"/tmp/{key}.wav")
                    # Read back
                    with engine._zoom_lock:
                        val = engine._zoom_cache.get(key)
                    results.append((thread_id, i, val is not None))
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Thread errors: {errors}"
        assert len(results) == 8 * 50
        assert all(ok for _, _, ok in results)
        assert len(engine._zoom_cache) == 8 * 50

    def test_concurrent_clear_doesnt_crash(self):
        """Clearing cache while other threads write shouldn't crash."""
        engine = _make_engine()
        errors = []

        def writer():
            try:
                for i in range(100):
                    with engine._zoom_lock:
                        engine._zoom_cache[f"w_{i}"] = Path(f"/tmp/w_{i}.wav")
            except Exception as exc:
                errors.append(exc)

        def clearer():
            try:
                for _ in range(10):
                    engine.clear_zoom_cache()
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=writer),
            threading.Thread(target=clearer),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
