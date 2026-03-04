"""
SyncLab Test Suite — Shared Fixtures
Provides synthetic audio generators and config helpers for all test modules.
"""

import numpy as np
import pytest
from pathlib import Path
from scipy.io import wavfile

from synclab.config import DEFAULT_CONFIG


# ---------------------------------------------------------------------------
# Audio signal generators
# ---------------------------------------------------------------------------

@pytest.fixture
def make_sine():
    """Generate a sine wave signal."""
    def _make(freq=440.0, duration=1.0, sr=8000, amplitude=0.5):
        t = np.arange(int(sr * duration)) / sr
        return (amplitude * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    return _make


@pytest.fixture
def make_chirp():
    """Generate a frequency sweep (chirp) signal."""
    def _make(f0=200, f1=3000, duration=5.0, sr=8000, amplitude=0.5):
        t = np.arange(int(sr * duration)) / sr
        phase = 2 * np.pi * (f0 * t + (f1 - f0) * t**2 / (2 * duration))
        return (amplitude * np.sin(phase)).astype(np.float32)
    return _make


@pytest.fixture
def make_speech_like():
    """Generate a speech-like signal: random noise bursts with silences."""
    def _make(duration=5.0, sr=8000, speech_ratio=0.5, seed=42):
        rng = np.random.RandomState(seed)
        n_samples = int(sr * duration)
        signal = np.zeros(n_samples, dtype=np.float32)

        # Create bursts of "speech" (filtered noise)
        frame_len = int(sr * 0.1)  # 100ms frames
        n_frames = n_samples // frame_len

        for i in range(n_frames):
            if rng.random() < speech_ratio:
                start = i * frame_len
                end = min(start + frame_len, n_samples)
                noise = rng.randn(end - start).astype(np.float32) * 0.3
                signal[start:end] = noise

        return signal
    return _make


@pytest.fixture
def make_offset_pair():
    """Generate a pair of signals where one is embedded in the other at a known offset.

    Returns (short_signal, long_signal, true_offset_seconds).
    The short_signal appears at position true_offset in the long_signal.
    """
    def _make(signal_duration=3.0, total_duration=30.0, offset_sec=10.0,
              sr=8000, noise_level=0.01, seed=42):
        rng = np.random.RandomState(seed)

        # Short signal (like camera audio)
        short_len = int(sr * signal_duration)
        short = rng.randn(short_len).astype(np.float32) * 0.5

        # Long signal (like zoom audio) — embed short at offset
        long_len = int(sr * total_duration)
        long_signal = rng.randn(long_len).astype(np.float32) * noise_level

        offset_samples = int(offset_sec * sr)
        end = min(offset_samples + short_len, long_len)
        actual_len = end - offset_samples
        long_signal[offset_samples:end] += short[:actual_len]

        return short, long_signal, offset_sec
    return _make


# ---------------------------------------------------------------------------
# Config fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def default_config():
    """Return a fresh copy of DEFAULT_CONFIG."""
    return dict(DEFAULT_CONFIG)


# ---------------------------------------------------------------------------
# WAV file helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_wav(tmp_path):
    """Write a numpy array to a temporary WAV file. Returns a factory function."""
    def _make(data, sr=8000, name="test.wav"):
        path = tmp_path / name
        # Convert float32 to int16 for WAV writing
        if data.dtype == np.float32 or data.dtype == np.float64:
            int_data = np.clip(data * 32767, -32768, 32767).astype(np.int16)
        else:
            int_data = data
        wavfile.write(str(path), sr, int_data)
        return path
    return _make


# ---------------------------------------------------------------------------
# Mock media info
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_video_info():
    """Create a mock media info dict (without calling ffprobe)."""
    def _make(duration=60.0, has_audio=True, width=1920, height=1080,
              fps=29.97, name="test_video.mp4"):
        return {
            "path": Path(f"/fake/{name}"),
            "name": name,
            "duration": duration,
            "has_audio": has_audio,
            "width": width,
            "height": height,
            "fps": fps,
            "sample_rate": 48000,
            "channels": 2,
            "video_streams": 1,
            "audio_streams": 1 if has_audio else 0,
        }
    return _make


@pytest.fixture
def mock_wav_by_type():
    """Create a mock wav_by_type dict for audio tracks."""
    def _make(duration=600.0, track_types=None):
        if track_types is None:
            track_types = ["_Tr1", "_LR"]
        result = {}
        for tt in track_types:
            result[tt] = {
                "path": Path(f"/fake/ZOOM0001{tt}.WAV"),
                "name": f"ZOOM0001{tt}.WAV",
                "duration": duration,
                "sample_rate": 48000,
                "channels": 1 if tt != "_LR" else 2,
            }
        return result
    return _make
