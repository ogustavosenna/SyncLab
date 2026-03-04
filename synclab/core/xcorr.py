"""
Cross-correlation functions for audio synchronisation.

Extracted from engine.py during v1.3.0 refactoring.
Provides both raw sample-level and envelope-based cross-correlation,
each with optional windowed variants for predicted-offset search.

All functions are stateless: they receive ``sr`` (sample rate)
as an explicit parameter instead of reading it from an engine
instance.

Dependencies: numpy, scipy (only).
"""

from __future__ import annotations

import numpy as np
from scipy import signal

from synclab.core.dsp import (
    parabolic_interpolation,
    compute_peak_ratio,
    compute_envelope,
)


# ---------------------------------------------------------------------------
# Raw FFT cross-correlation
# ---------------------------------------------------------------------------

def xcorr(
    cam: np.ndarray,
    zoom: np.ndarray,
    sr: int,
) -> tuple[float, float, float]:
    """Normalised cross-correlation: find *cam* inside *zoom*.

    Uses ``scipy.signal.fftconvolve`` for O(n log n) performance.
    A parabolic (3-point) interpolation around the peak yields
    sub-sample precision.

    Parameters
    ----------
    cam : ndarray
        Camera audio signal.
    zoom : ndarray
        Zoom (external recorder) audio signal.
    sr : int
        Sample rate in Hz.

    Returns
    -------
    offset_seconds : float
        Positive means the camera starts at position *offset* inside
        the Zoom recording.
    confidence : float
        Peak of the normalised correlation (0--1).
    peak_ratio : float
        Ratio of highest peak to second-highest peak.  Higher values
        indicate a more distinct (less ambiguous) match.
    """
    cam = np.asarray(cam, dtype=np.float64)
    zoom = np.asarray(zoom, dtype=np.float64)

    # Zero-mean
    cam = cam - np.mean(cam)
    zoom = zoom - np.mean(zoom)

    # Check energy
    std_c = np.std(cam)
    std_z = np.std(zoom)
    if std_c < 1e-10 or std_z < 1e-10:
        return 0.0, 0.0, 0.0

    # Normalised cross-correlation via FFT
    corr = signal.fftconvolve(zoom, cam[::-1], mode="full")
    corr = corr / (len(cam) * std_c * std_z)

    # Find peak
    pk = int(np.argmax(corr))
    conf = float(corr[pk])
    offset_samp = float(pk - (len(cam) - 1))

    # Parabolic interpolation for sub-sample accuracy
    offset_samp += parabolic_interpolation(corr, pk)

    # Peak-to-second-peak ratio
    min_gap = max(int(0.5 * sr), 1)
    pr = compute_peak_ratio(corr, pk, conf, min_gap)

    return offset_samp / sr, max(conf, 0.0), pr


# ---------------------------------------------------------------------------
# Windowed cross-correlation
# ---------------------------------------------------------------------------

def xcorr_windowed(
    cam: np.ndarray,
    zoom: np.ndarray,
    sr: int,
    predicted_offset: float,
    sync_window_sec: float = 30,
) -> tuple[float, float, float]:
    """Cross-correlation in a window around *predicted_offset*.

    Extracts a segment of the Zoom audio centred on the predicted
    offset and runs :func:`xcorr` on that segment.  The returned
    offset is expressed in global Zoom time.

    Parameters
    ----------
    cam : ndarray
        Camera audio signal.
    zoom : ndarray
        Full Zoom audio signal.
    sr : int
        Sample rate in Hz.
    predicted_offset : float
        Expected offset in seconds.
    sync_window_sec : float
        Half-width of the search window in seconds (default 30).

    Returns
    -------
    offset_global : float
        Offset in seconds relative to the start of *zoom*.
    confidence : float
        Peak normalised correlation.
    peak_ratio : float
        Peak distinctness ratio.
    """
    cam_dur_sec = len(cam) / sr

    # Window boundaries in the Zoom signal
    win_start = max(0, int((predicted_offset - sync_window_sec) * sr))
    win_end = min(
        len(zoom),
        int((predicted_offset + cam_dur_sec + sync_window_sec) * sr),
    )

    if win_end <= win_start:
        return 0.0, 0.0, 0.0

    zoom_win = zoom[win_start:win_end]
    if len(zoom_win) < len(cam) // 2:
        return 0.0, 0.0, 0.0

    # Cross-correlation inside the window
    offset_local, conf, peak_ratio = xcorr(cam, zoom_win, sr)

    # Convert local offset to global (relative to Zoom start)
    offset_global = offset_local + (win_start / sr)
    return offset_global, conf, peak_ratio


# ---------------------------------------------------------------------------
# Envelope cross-correlation
# ---------------------------------------------------------------------------

def xcorr_envelope(
    cam: np.ndarray,
    zoom: np.ndarray,
    sr: int,
    hop: int = 200,
) -> tuple[float, float, float]:
    """Cross-correlate amplitude envelopes.

    More robust than raw sample-level cross-correlation when the
    camera and external recorder use different microphones (different
    frequency responses, noise floors, etc.).

    Parameters
    ----------
    cam : ndarray
        Camera audio.
    zoom : ndarray
        Zoom audio.
    sr : int
        Sample rate in Hz.
    hop : int
        Envelope frame size in samples.

    Returns
    -------
    offset_seconds : float
        Time offset in seconds.
    confidence : float
        Peak normalised correlation.
    peak_ratio : float
        Peak distinctness ratio.
    """
    env_c = compute_envelope(cam, hop)
    env_z = compute_envelope(zoom, hop)

    if len(env_c) < 2 or len(env_z) < 2:
        return 0.0, 0.0, 0.0

    env_c = env_c.astype(np.float64)
    env_z = env_z.astype(np.float64)
    env_c = env_c - np.mean(env_c)
    env_z = env_z - np.mean(env_z)

    std_c = np.std(env_c)
    std_z = np.std(env_z)
    if std_c < 1e-10 or std_z < 1e-10:
        return 0.0, 0.0, 0.0

    corr = signal.fftconvolve(env_z, env_c[::-1], mode="full")
    corr = corr / (len(env_c) * std_c * std_z)

    pk = int(np.argmax(corr))
    conf = float(corr[pk])
    offset_frames = float(pk - (len(env_c) - 1))

    # Parabolic interpolation for sub-frame precision
    offset_frames += parabolic_interpolation(corr, pk)

    # Peak-to-second-peak ratio
    # Envelope operates at reduced sample rate (sr / hop)
    env_sr = sr / hop
    min_gap = max(int(0.5 * env_sr), 1)
    pr = compute_peak_ratio(corr, pk, conf, min_gap)

    offset_sec = offset_frames * hop / sr
    return offset_sec, max(conf, 0.0), pr


# ---------------------------------------------------------------------------
# Windowed envelope cross-correlation
# ---------------------------------------------------------------------------

def xcorr_envelope_windowed(
    cam: np.ndarray,
    zoom: np.ndarray,
    sr: int,
    predicted_offset: float,
    sync_window_sec: float = 30,
    hop: int = 200,
) -> tuple[float, float, float]:
    """Windowed envelope cross-correlation around *predicted_offset*.

    Combines the robustness of envelope correlation with the
    efficiency of a limited search window.

    Parameters
    ----------
    cam : ndarray
        Camera audio (full).
    zoom : ndarray
        Zoom audio (full).
    sr : int
        Sample rate in Hz.
    predicted_offset : float
        Expected offset in seconds.
    sync_window_sec : float
        Half-width of the window in seconds (default 30).
    hop : int
        Envelope frame size in samples.

    Returns
    -------
    offset_global : float
        Offset in seconds relative to the start of *zoom*.
    confidence : float
        Peak normalised correlation of the envelopes.
    peak_ratio : float
        Peak distinctness ratio.
    """
    cam_dur_sec = len(cam) / sr

    win_start = max(0, int((predicted_offset - sync_window_sec) * sr))
    win_end = min(
        len(zoom),
        int((predicted_offset + cam_dur_sec + sync_window_sec) * sr),
    )

    if win_end <= win_start:
        return 0.0, 0.0, 0.0

    zoom_win = zoom[win_start:win_end]
    if len(zoom_win) < len(cam) // 2:
        return 0.0, 0.0, 0.0

    offset_local, conf, peak_ratio = xcorr_envelope(
        cam, zoom_win, sr, hop,
    )
    offset_global = offset_local + (win_start / sr)
    return offset_global, conf, peak_ratio
