"""
Tests for full sync_with_zoom integration.
Priority 7: Golden test — verifies the complete 4-stage pipeline.

This test writes synthetic WAV files to disk and runs the full sync pipeline.
It's the most critical characterization test for refactoring safety.
"""

import numpy as np
import pytest
from pathlib import Path
from scipy.io import wavfile

from synclab.config import get_config
from synclab.core.engine import SyncEngine


@pytest.fixture
def engine():
    """Create a SyncEngine with test-friendly config."""
    config = get_config(
        sample_rate=8000,
        max_camera_sec=60,
        sync_window_sec=30,
        threshold=0.05,
        multi_slice_enabled=False,  # Simpler for synthetic tests
        spectral_whiten=False,      # Not needed for clean synthetic signals
    )
    return SyncEngine(config)


def _write_wav(path, data, sr=8000):
    """Write float32 data to WAV file as int16."""
    int_data = np.clip(data * 32767, -32768, 32767).astype(np.int16)
    wavfile.write(str(path), sr, int_data)


# ---------------------------------------------------------------------------
# Full sync_with_zoom integration
# ---------------------------------------------------------------------------

class TestSyncWithZoom:
    """Integration test for the complete 4-stage sync pipeline."""

    def test_recovers_known_offset(self, engine, tmp_path):
        """Given synthetic cam+zoom WAVs with known offset, should recover it.

        This is the GOLDEN TEST for refactoring safety.
        """
        sr = 8000
        rng = np.random.RandomState(42)

        # Camera audio: 5 seconds of "speech-like" signal
        cam_duration = 5.0
        cam_samples = int(sr * cam_duration)
        cam_signal = rng.randn(cam_samples).astype(np.float32) * 0.4

        # Add some structure (bursts) to make it correlatable
        for i in range(0, cam_samples, sr):
            end = min(i + sr // 2, cam_samples)
            cam_signal[i:end] *= 3.0  # Loud burst every second

        # Zoom audio: 30 seconds with cam embedded at offset 10s
        zoom_duration = 30.0
        zoom_samples = int(sr * zoom_duration)
        true_offset = 10.0
        offset_samples = int(true_offset * sr)

        zoom_signal = rng.randn(zoom_samples).astype(np.float32) * 0.01  # Quiet background
        zoom_signal[offset_samples:offset_samples + cam_samples] += cam_signal

        # Write WAVs
        cam_wav = tmp_path / "camera.wav"
        zoom_wav = tmp_path / "ZOOM0001_Tr1.WAV"
        _write_wav(cam_wav, cam_signal, sr)
        _write_wav(zoom_wav, zoom_signal, sr)

        # Run sync
        result = engine.sync_with_zoom(
            cam_wav=str(cam_wav),
            zoom_wav_files=[str(zoom_wav)],
            temp_dir=tmp_path,
            predicted_offset=None,  # No timestamp prediction
        )

        assert result is not None, "sync_with_zoom returned None"
        assert "offset" in result, f"Result missing 'offset': {result.keys()}"
        assert "confidence" in result, f"Result missing 'confidence': {result.keys()}"

        recovered_offset = result["offset"]
        confidence = result["confidence"]

        # Allow 1 second tolerance for synthetic data
        assert abs(recovered_offset - true_offset) < 1.0, (
            f"Offset error too large: recovered={recovered_offset:.2f}s, "
            f"true={true_offset:.2f}s (error={abs(recovered_offset - true_offset):.2f}s)"
        )
        assert confidence > 0.05, f"Confidence too low: {confidence:.3f}"

    def test_with_predicted_offset(self, engine, tmp_path):
        """With a good predicted offset, should use windowed search and be more precise."""
        sr = 8000
        rng = np.random.RandomState(123)

        cam_duration = 3.0
        cam_samples = int(sr * cam_duration)
        cam_signal = rng.randn(cam_samples).astype(np.float32) * 0.5

        # Add structure
        for i in range(0, cam_samples, sr):
            end = min(i + sr // 2, cam_samples)
            cam_signal[i:end] *= 2.0

        zoom_duration = 60.0
        zoom_samples = int(sr * zoom_duration)
        true_offset = 25.0
        offset_samples = int(true_offset * sr)

        zoom_signal = rng.randn(zoom_samples).astype(np.float32) * 0.01
        zoom_signal[offset_samples:offset_samples + cam_samples] += cam_signal

        cam_wav = tmp_path / "camera2.wav"
        zoom_wav = tmp_path / "ZOOM0002_Tr1.WAV"
        _write_wav(cam_wav, cam_signal, sr)
        _write_wav(zoom_wav, zoom_signal, sr)

        result = engine.sync_with_zoom(
            cam_wav=str(cam_wav),
            zoom_wav_files=[str(zoom_wav)],
            temp_dir=tmp_path,
            predicted_offset=25.5,  # Close to true offset
        )

        assert result is not None
        recovered_offset = result["offset"]
        assert abs(recovered_offset - true_offset) < 0.5, (
            f"With prediction, expected better accuracy: "
            f"recovered={recovered_offset:.2f}s, true={true_offset:.2f}s"
        )

    def test_returns_diagnostics(self, engine, tmp_path):
        """Result should include diagnostics dict with stage info."""
        sr = 8000
        rng = np.random.RandomState(456)

        cam = rng.randn(sr * 3).astype(np.float32) * 0.3
        zoom = np.zeros(sr * 20, dtype=np.float32)
        zoom[sr * 5:sr * 5 + len(cam)] += cam

        cam_wav = tmp_path / "cam_diag.wav"
        zoom_wav = tmp_path / "ZOOM_diag_Tr1.WAV"
        _write_wav(cam_wav, cam, sr)
        _write_wav(zoom_wav, zoom, sr)

        result = engine.sync_with_zoom(
            cam_wav=str(cam_wav),
            zoom_wav_files=[str(zoom_wav)],
            temp_dir=tmp_path,
        )

        assert "diagnostics" in result, "Result should contain diagnostics"
        diag = result["diagnostics"]
        assert "stages" in diag, "Diagnostics should contain 'stages' list"

    def test_result_has_peak_ratio(self, engine, tmp_path):
        """Result should include peak_ratio metric."""
        sr = 8000
        rng = np.random.RandomState(789)

        cam = rng.randn(sr * 3).astype(np.float32) * 0.3
        zoom = np.zeros(sr * 20, dtype=np.float32)
        zoom[sr * 8:sr * 8 + len(cam)] += cam

        cam_wav = tmp_path / "cam_pr.wav"
        zoom_wav = tmp_path / "ZOOM_pr_Tr1.WAV"
        _write_wav(cam_wav, cam, sr)
        _write_wav(zoom_wav, zoom, sr)

        result = engine.sync_with_zoom(
            cam_wav=str(cam_wav),
            zoom_wav_files=[str(zoom_wav)],
            temp_dir=tmp_path,
        )

        assert "peak_ratio" in result, "Result should contain peak_ratio"
        assert result["peak_ratio"] > 0, "peak_ratio should be positive"
