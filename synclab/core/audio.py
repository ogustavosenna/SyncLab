"""
SyncLab Core - Audio Utilities
Helper functions for audio extraction, loading, and filtering.
"""

import os
import subprocess
from pathlib import Path

from synclab.subprocess_utils import subprocess_hide_window

import numpy as np
from scipy import signal
from scipy.io import wavfile


def extract_wav(input_path, output_path, sample_rate=8000, max_sec=0):
    """Extract audio from any media file as mono WAV.

    Args:
        input_path: Source media file (video or audio).
        output_path: Destination WAV file path.
        sample_rate: Target sample rate in Hz.
        max_sec: Maximum duration in seconds (0 = no limit).

    Returns:
        True if extraction succeeded, False otherwise.
    """
    try:
        cmd = [
            "ffmpeg", "-y", "-v", "quiet",
            "-i", str(input_path),
            "-vn", "-ac", "1", "-ar", str(sample_rate),
        ]
        if max_sec > 0:
            cmd.extend(["-t", str(max_sec)])
        cmd.extend(["-f", "wav", str(output_path)])

        timeout = max(600, max_sec * 2) if max_sec > 0 else 1800
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **subprocess_hide_window())
        return (
            r.returncode == 0
            and Path(output_path).exists()
            and Path(output_path).stat().st_size > 1000
        )
    except Exception:
        return False


def load_wav(file_path):
    """Load a WAV file as float32 mono numpy array.

    Args:
        file_path: Path to WAV file.

    Returns:
        Tuple of (data_array, sample_rate) or (None, None) on error.
    """
    try:
        sr, data = wavfile.read(str(file_path))
        if data.dtype == np.int16:
            data = data.astype(np.float32) / 32768.0
        elif data.dtype == np.int32:
            data = data.astype(np.float32) / 2147483648.0
        elif data.dtype != np.float32:
            data = data.astype(np.float32)
        if data.ndim > 1:
            data = np.mean(data, axis=1)
        return data, sr
    except Exception:
        return None, None


def bandpass_filter(data, sample_rate, low_hz=200, high_hz=4000, order=4):
    """Apply Butterworth bandpass filter to isolate voice frequencies.

    Args:
        data: Audio signal as numpy array.
        sample_rate: Sample rate in Hz.
        low_hz: Lower cutoff frequency.
        high_hz: Upper cutoff frequency.
        order: Filter order.

    Returns:
        Filtered audio signal.
    """
    try:
        nyq = sample_rate / 2.0
        low = max(low_hz / nyq, 0.001)
        high = min(high_hz / nyq, 0.999)
        if low >= high:
            return data
        b, a = signal.butter(order, [low, high], btype="band")
        return signal.filtfilt(b, a, data)
    except Exception:
        return data


def compute_speech_ratio(data, sample_rate=8000, frame_ms=25, energy_threshold_db=-40):
    """Simple energy-based Voice Activity Detection.

    Counts the fraction of frames whose RMS energy exceeds the threshold.
    Used to detect if camera audio is too weak/silent to correlate.

    Args:
        data: Audio signal as numpy array (float).
        sample_rate: Sample rate in Hz.
        frame_ms: Frame length in milliseconds.
        energy_threshold_db: Energy threshold in dB relative to full scale.

    Returns:
        Float between 0.0 and 1.0: fraction of frames with speech-level energy.
    """
    frame_len = int(sample_rate * frame_ms / 1000)
    if len(data) < frame_len:
        return 0.0

    n_frames = len(data) // frame_len
    if n_frames < 1:
        return 0.0

    frames = data[: n_frames * frame_len].reshape(n_frames, frame_len)
    rms = np.sqrt(np.mean(frames ** 2, axis=1))

    # Convert threshold from dB to linear
    threshold = 10 ** (energy_threshold_db / 20.0)

    active = np.sum(rms > threshold)
    return float(active / n_frames)


def spectral_whiten(data, smoothing_width=20):
    """Apply spectral whitening to reduce microphone signature.

    Divides the FFT magnitude by a smoothed version of itself,
    preserving phase. This makes cross-correlation less sensitive
    to frequency response differences between microphones.

    Args:
        data: Audio signal as numpy array.
        smoothing_width: Width of the moving average kernel for
            smoothing the magnitude spectrum.

    Returns:
        Whitened audio signal (same length as input).
    """
    n = len(data)
    if n < smoothing_width * 2:
        return data

    spectrum = np.fft.rfft(data)
    magnitude = np.abs(spectrum)
    phase = np.angle(spectrum)

    # Smooth the magnitude with a moving average
    kernel = np.ones(smoothing_width) / smoothing_width
    smoothed = np.convolve(magnitude, kernel, mode="same")

    # Avoid division by zero
    smoothed = np.maximum(smoothed, 1e-10)

    # Whiten: divide magnitude by smoothed version
    whitened_mag = magnitude / smoothed

    # Reconstruct
    whitened_spectrum = whitened_mag * np.exp(1j * phase)
    result = np.fft.irfft(whitened_spectrum, n=n)

    return result.astype(np.float32)


def classify_track(wav_path, track_types):
    """Classify a WAV file by its track type based on filename.

    Args:
        wav_path: Path to WAV file.
        track_types: List of track type suffixes (e.g., ["_Tr1", "_LR"]).

    Returns:
        Matching track type string, or "_Other".
    """
    name = Path(wav_path).stem
    for tt in track_types:
        if tt in name:
            return tt
    return "_Other"


def safe_remove(path):
    """Safely remove a file, ignoring errors."""
    try:
        if path and Path(path).exists():
            os.remove(path)
    except Exception:
        pass


def format_duration(seconds):
    """Format duration as human-readable string.

    Args:
        seconds: Duration in seconds.

    Returns:
        Formatted string (e.g., "1m32s", "2h05m").
    """
    if seconds <= 0:
        return "0s"
    if seconds < 60:
        return f"{seconds:.1f}s"
    if seconds < 3600:
        m, sec = divmod(int(seconds), 60)
        return f"{m}m{sec:02d}s"
    h, rem = divmod(int(seconds), 3600)
    m, sec = divmod(rem, 60)
    return f"{h}h{m:02d}m"
