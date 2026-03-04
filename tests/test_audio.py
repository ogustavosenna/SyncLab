"""
Tests for synclab/core/audio.py
Priority 1: Pure functions, foundation of everything.
"""

import numpy as np
import pytest
from pathlib import Path

from synclab.core.audio import (
    bandpass_filter,
    compute_speech_ratio,
    spectral_whiten,
    classify_track,
    format_duration,
    load_wav,
)


# ---------------------------------------------------------------------------
# bandpass_filter
# ---------------------------------------------------------------------------

class TestBandpassFilter:
    """Test Butterworth bandpass filter for voice frequency isolation."""

    def test_passes_voice_frequency(self, make_sine):
        """A 1 kHz sine (within 200-4000 Hz) should pass through with minimal loss."""
        signal = make_sine(freq=1000, duration=1.0, sr=8000, amplitude=1.0)
        filtered = bandpass_filter(signal, sample_rate=8000, low_hz=200, high_hz=4000)

        # Energy should be preserved (within 20% tolerance for filter roll-off)
        input_energy = np.sqrt(np.mean(signal ** 2))
        output_energy = np.sqrt(np.mean(filtered ** 2))
        assert output_energy > input_energy * 0.7, (
            f"Voice-range signal lost too much energy: {output_energy:.3f} vs {input_energy:.3f}"
        )

    def test_attenuates_low_frequency(self, make_sine):
        """A 50 Hz sine (below 200 Hz cutoff) should be strongly attenuated."""
        signal = make_sine(freq=50, duration=1.0, sr=8000, amplitude=1.0)
        filtered = bandpass_filter(signal, sample_rate=8000, low_hz=200, high_hz=4000)

        input_energy = np.sqrt(np.mean(signal ** 2))
        output_energy = np.sqrt(np.mean(filtered ** 2))
        assert output_energy < input_energy * 0.3, (
            f"Low-freq signal not attenuated enough: {output_energy:.3f} vs {input_energy:.3f}"
        )

    def test_returns_same_length(self, make_sine):
        """Output must have the same number of samples as input."""
        signal = make_sine(freq=440, duration=2.0, sr=8000)
        filtered = bandpass_filter(signal, sample_rate=8000)
        assert len(filtered) == len(signal)

    def test_invalid_range_returns_original(self):
        """If low >= high, should return the original signal unmodified."""
        signal = np.random.randn(8000).astype(np.float32)
        result = bandpass_filter(signal, sample_rate=8000, low_hz=4000, high_hz=200)
        np.testing.assert_array_equal(result, signal)

    def test_empty_signal(self):
        """Empty array should not crash."""
        signal = np.array([], dtype=np.float32)
        result = bandpass_filter(signal, sample_rate=8000)
        assert len(result) == 0


# ---------------------------------------------------------------------------
# compute_speech_ratio
# ---------------------------------------------------------------------------

class TestComputeSpeechRatio:
    """Test energy-based Voice Activity Detection."""

    def test_silence_returns_zero(self):
        """All-zero signal should have speech_ratio = 0.0."""
        silence = np.zeros(16000, dtype=np.float32)
        ratio = compute_speech_ratio(silence, sample_rate=8000)
        assert ratio == 0.0

    def test_loud_signal_returns_high(self):
        """A loud signal should have high speech_ratio."""
        loud = np.ones(16000, dtype=np.float32) * 0.5
        ratio = compute_speech_ratio(loud, sample_rate=8000)
        assert ratio > 0.9, f"Expected high speech ratio for loud signal, got {ratio}"

    def test_mixed_signal(self, make_speech_like):
        """Speech-like signal with 50% activity should return ~0.5."""
        signal = make_speech_like(duration=5.0, sr=8000, speech_ratio=0.5)
        ratio = compute_speech_ratio(signal, sample_rate=8000)
        # Allow wide tolerance since it's stochastic
        assert 0.1 < ratio < 0.9, f"Expected moderate speech ratio, got {ratio}"

    def test_returns_float_0_to_1(self, make_speech_like):
        """Result must be a float between 0.0 and 1.0."""
        signal = make_speech_like(duration=2.0, sr=8000)
        ratio = compute_speech_ratio(signal, sample_rate=8000)
        assert isinstance(ratio, float)
        assert 0.0 <= ratio <= 1.0

    def test_short_signal_returns_zero(self):
        """Signal shorter than one frame should return 0.0."""
        short = np.ones(10, dtype=np.float32)
        ratio = compute_speech_ratio(short, sample_rate=8000, frame_ms=25)
        assert ratio == 0.0


# ---------------------------------------------------------------------------
# spectral_whiten
# ---------------------------------------------------------------------------

class TestSpectralWhiten:
    """Test spectral whitening for reducing microphone signatures."""

    def test_preserves_length(self, make_sine):
        """Output must have the same length as input."""
        signal = make_sine(freq=440, duration=1.0, sr=8000)
        whitened = spectral_whiten(signal)
        assert len(whitened) == len(signal)

    def test_flattens_spectrum(self, make_sine):
        """After whitening, the spectrum should be more uniform (flatter)."""
        signal = make_sine(freq=1000, duration=1.0, sr=8000)
        whitened = spectral_whiten(signal)

        # The original spectrum has a sharp peak at 1kHz.
        # After whitening, energy should be more spread out.
        orig_spec = np.abs(np.fft.rfft(signal))
        white_spec = np.abs(np.fft.rfft(whitened))

        # Coefficient of variation (std/mean) should decrease
        orig_cv = np.std(orig_spec) / (np.mean(orig_spec) + 1e-10)
        white_cv = np.std(white_spec) / (np.mean(white_spec) + 1e-10)
        assert white_cv < orig_cv, (
            f"Whitened spectrum not flatter: CV {white_cv:.2f} vs original {orig_cv:.2f}"
        )

    def test_returns_float32(self, make_sine):
        """Output should be float32."""
        signal = make_sine(freq=440, duration=1.0, sr=8000)
        whitened = spectral_whiten(signal)
        assert whitened.dtype == np.float32

    def test_short_signal_returns_original(self):
        """Very short signals should be returned unmodified."""
        short = np.array([1.0, 2.0, 3.0], dtype=np.float32)
        result = spectral_whiten(short, smoothing_width=20)
        np.testing.assert_array_equal(result, short)


# ---------------------------------------------------------------------------
# classify_track
# ---------------------------------------------------------------------------

class TestClassifyTrack:
    """Test WAV file classification by filename."""

    def test_tr1_track(self, tmp_path):
        """Files containing '_Tr1' should be classified as '_Tr1'."""
        wav = tmp_path / "ZOOM0001_Tr1.WAV"
        wav.touch()
        result = classify_track(wav, ["_Tr1", "_Tr2", "_LR"])
        assert result == "_Tr1"

    def test_lr_track(self, tmp_path):
        """Files containing '_LR' should be classified as '_LR'."""
        wav = tmp_path / "ZOOM0003_LR.WAV"
        wav.touch()
        result = classify_track(wav, ["_Tr1", "_Tr2", "_LR"])
        assert result == "_LR"

    def test_unknown_track(self, tmp_path):
        """Files without known suffixes should return '_Other'."""
        wav = tmp_path / "random_audio.WAV"
        wav.touch()
        result = classify_track(wav, ["_Tr1", "_Tr2", "_LR"])
        assert result == "_Other"

    def test_first_match_wins(self, tmp_path):
        """If multiple suffixes match, the first in the list wins."""
        wav = tmp_path / "ZOOM0001_Tr1_LR.WAV"
        wav.touch()
        result = classify_track(wav, ["_Tr1", "_LR"])
        assert result == "_Tr1"


# ---------------------------------------------------------------------------
# format_duration
# ---------------------------------------------------------------------------

class TestFormatDuration:
    """Test human-readable duration formatting."""

    def test_zero(self):
        assert format_duration(0) == "0s"

    def test_negative(self):
        assert format_duration(-5) == "0s"

    def test_seconds_only(self):
        assert format_duration(45.3) == "45.3s"

    def test_minutes_seconds(self):
        assert format_duration(92) == "1m32s"

    def test_hours_minutes(self):
        assert format_duration(7500) == "2h05m"

    def test_exactly_60(self):
        result = format_duration(60)
        assert result == "1m00s"


# ---------------------------------------------------------------------------
# load_wav
# ---------------------------------------------------------------------------

class TestLoadWav:
    """Test WAV file loading."""

    def test_load_int16_wav(self, tmp_wav, make_sine):
        """Load a standard int16 WAV file."""
        signal = make_sine(freq=440, duration=0.5, sr=8000)
        path = tmp_wav(signal, sr=8000, name="test_int16.wav")

        data, sr = load_wav(path)
        assert data is not None
        assert sr == 8000
        assert data.dtype == np.float32
        assert len(data) == len(signal)
        # Values should be in [-1, 1] range
        assert np.max(np.abs(data)) <= 1.0

    def test_load_mono(self, tmp_wav, make_sine):
        """Loaded data should be mono (1D array)."""
        signal = make_sine(freq=440, duration=0.5, sr=8000)
        path = tmp_wav(signal, sr=8000)
        data, sr = load_wav(path)
        assert data.ndim == 1

    def test_nonexistent_file(self):
        """Non-existent file should return (None, None)."""
        data, sr = load_wav("/nonexistent/path/file.wav")
        assert data is None
        assert sr is None
