"""
DSP utility functions — stateless, pure helper functions.

Extracted from engine.py during v1.3.0 refactoring.
These functions are the mathematical building blocks used by
the cross-correlation functions in xcorr.py.

Eliminates code duplication: parabolic_interpolation and
compute_peak_ratio were previously duplicated between
_xcorr and _xcorr_envelope in engine.py.

Dependencies: numpy (only).
"""

from __future__ import annotations

from typing import List

import numpy as np


# ---------------------------------------------------------------------------
# Parabolic interpolation
# ---------------------------------------------------------------------------

def parabolic_interpolation(
    corr: np.ndarray,
    pk: int,
) -> float:
    """Parabolic (3-point) interpolation around a correlation peak.

    Given a discrete correlation array and the index of its maximum,
    fits a parabola through the peak and its two neighbours to obtain
    sub-sample (or sub-frame) precision.

    Parameters
    ----------
    corr : ndarray
        Correlation array.
    pk : int
        Index of the peak in *corr*.

    Returns
    -------
    float
        Sub-sample correction to add to *pk* for the refined position.
        Returns 0.0 if the peak is at an edge or interpolation is
        degenerate.
    """
    if 0 < pk < len(corr) - 1:
        y0 = float(corr[pk - 1])
        y1 = float(corr[pk])
        y2 = float(corr[pk + 1])
        denom = 2.0 * (2 * y1 - y0 - y2)
        if abs(denom) > 1e-10:
            return (y0 - y2) / denom
    return 0.0


# ---------------------------------------------------------------------------
# Peak-to-second-peak ratio
# ---------------------------------------------------------------------------

def compute_peak_ratio(
    corr: np.ndarray,
    pk: int,
    conf: float,
    min_gap: int,
) -> float:
    """Compute the peak-to-second-peak ratio.

    Masks the region around the primary peak and finds the next
    highest peak.  The ratio measures match distinctness: higher
    values indicate a more unambiguous match.

    Parameters
    ----------
    corr : ndarray
        Correlation array.
    pk : int
        Index of the primary peak.
    conf : float
        Value of the primary peak (normalised correlation).
    min_gap : int
        Minimum distance (in samples or frames) between primary
        and secondary peak.

    Returns
    -------
    float
        Ratio of primary to secondary peak.  Returns ``inf`` if
        no secondary peak is found or the array is too short.
    """
    if len(corr) <= 10:
        return float("inf")

    corr_copy = corr.copy()
    mask_start = max(0, pk - min_gap)
    mask_end = min(len(corr_copy), pk + min_gap)
    corr_copy[mask_start:mask_end] = 0.0
    pk2 = int(np.argmax(corr_copy))
    conf2 = float(corr_copy[pk2])
    if conf2 > 1e-10:
        return conf / conf2
    return float("inf")


# ---------------------------------------------------------------------------
# RMS amplitude envelope
# ---------------------------------------------------------------------------

def compute_envelope(
    data: np.ndarray,
    hop: int = 200,
) -> np.ndarray:
    """Compute the RMS amplitude envelope of *data*.

    At the default analysis rate of 8 kHz, a hop of 200 samples
    gives 25 ms frames -- capturing *when* there is energy (speech
    vs. silence) rather than frequency content.  This makes the
    envelope robust across different microphones recording the same
    acoustic event.

    Parameters
    ----------
    data : ndarray
        Audio signal.
    hop : int
        Frame length in samples.

    Returns
    -------
    ndarray
        RMS amplitude per frame.
    """
    n = len(data) // hop
    if n < 2:
        return np.array([np.sqrt(np.mean(data**2))])
    frames = data[: n * hop].reshape(n, hop)
    return np.sqrt(np.mean(frames**2, axis=1))


# ---------------------------------------------------------------------------
# Multi-slice extraction
# ---------------------------------------------------------------------------

def extract_slices(
    cam_bp: np.ndarray,
    sr: int,
    count: int = 3,
    duration_sec: int = 20,
) -> List[tuple[np.ndarray, int]]:
    """Extract multi-slice segments from camera audio.

    Returns list of (slice_data, start_sample) tuples for
    beginning, middle, and end of the recording.

    Parameters
    ----------
    cam_bp : ndarray
        Bandpass-filtered camera audio.
    sr : int
        Sample rate in Hz.
    count : int
        Number of slices to extract (default 3).
    duration_sec : int
        Duration of each slice in seconds (default 20).

    Returns
    -------
    list of (ndarray, int)
        Each element is (slice_data, start_sample_index).
    """
    slice_samples = duration_sec * sr
    total = len(cam_bp)

    if total <= slice_samples:
        return [(cam_bp, 0)]

    slices = []
    # Beginning
    slices.append((cam_bp[:slice_samples], 0))
    # Middle
    mid_start = (total - slice_samples) // 2
    slices.append((cam_bp[mid_start:mid_start + slice_samples], mid_start))
    # End
    end_start = total - slice_samples
    slices.append((cam_bp[end_start:], end_start))

    return slices[:count]


# ---------------------------------------------------------------------------
# Multi-slice consensus
# ---------------------------------------------------------------------------

def multi_slice_consensus(
    results: List[tuple[float, float, float]],
    tolerance: float = 0.5,
) -> tuple[float, float, float]:
    """Find consensus offset from multiple slices.

    Parameters
    ----------
    results : list of (offset, confidence, peak_ratio) tuples
    tolerance : float
        Maximum disagreement in seconds between two slices
        to consider them "agreeing".

    Returns
    -------
    (offset, confidence, peak_ratio) : the consensus result.
        If 2+ slices agree within tolerance, returns the average
        offset, max confidence, and max peak_ratio of the group.
        Otherwise, returns the single best result by confidence.
    """
    if len(results) <= 1:
        return results[0] if results else (0.0, 0.0, 0.0)

    best_group: List[tuple[float, float, float]] = []
    best_group_conf = 0.0

    for i in range(len(results)):
        group = [results[i]]
        for j in range(len(results)):
            if i != j and abs(results[i][0] - results[j][0]) <= tolerance:
                group.append(results[j])
        if len(group) >= 2:
            max_conf = max(r[1] for r in group)
            if max_conf > best_group_conf:
                best_group = group
                best_group_conf = max_conf

    if best_group:
        avg_off = sum(r[0] for r in best_group) / len(best_group)
        max_conf = max(r[1] for r in best_group)
        max_pr = max(r[2] for r in best_group)
        return avg_off, max_conf, max_pr

    # No consensus: return the slice with highest confidence
    return max(results, key=lambda r: r[1])
