"""
Clock calibration and timestamp-based assignment.

Extracted from matcher.py during v1.3.0 refactoring.
Provides functions for:
  - Validating computed offsets
  - Calibrating clock offset between camera and recorder clocks
    via 3-pass search (coarse 15s -> fine 1s -> sub-second 0.1s)
  - Assigning videos to recorders based on temporal overlap

v1.3.0 eliminates code duplication: the 3-pass search logic
(previously duplicated between _calibrate_clock_offset and
_calibrate_subset) is now a single _three_pass_search() function.

Dependencies: datetime (only).
"""

from __future__ import annotations

import datetime
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Offset validation
# ---------------------------------------------------------------------------

def valid_offset(
    offset: float,
    video_duration: float,
    recorder_duration: float,
    max_camera_sec: float = 60,
) -> bool:
    """Check whether offset places the video within the recorder range.

    Parameters
    ----------
    offset : float
        Computed offset in seconds (video start within recorder).
    video_duration : float
        Duration of the video in seconds.
    recorder_duration : float
        Duration of the recorder audio in seconds.
    max_camera_sec : float
        Maximum camera audio used for matching (from config).

    Returns
    -------
    bool
        True if the offset is plausible.
    """
    if recorder_duration <= 0 or video_duration <= 0:
        return True
    cam_len = min(video_duration, max_camera_sec)
    if offset < -30:
        return False
    if offset > recorder_duration:
        return False
    if offset > 0 and offset + cam_len > recorder_duration + 15:
        return False
    return True


# ---------------------------------------------------------------------------
# Shared 3-pass search (eliminates duplication)
# ---------------------------------------------------------------------------

def _three_pass_search(
    valid_vt: List[tuple],
    valid_rt: List[tuple],
    default_tol: float = 15,
) -> Tuple[Optional[datetime.timedelta], int]:
    """Three-pass clock offset search shared by calibrate functions.

    Pass 1: Coarse search -24h..+24h in 15-second steps.
    Pass 2: Fine search +/-15s around best in 1-second steps.
    Pass 3: Sub-second refinement +/-1s in 0.1-second steps.

    Parameters
    ----------
    valid_vt : list of (index, datetime)
        Valid video timestamps with their indices.
    valid_rt : list of (index, start_datetime, end_datetime)
        Valid recorder time ranges with their indices.
    default_tol : float
        Tolerance in seconds for matching.

    Returns
    -------
    (timedelta_offset, match_count) or (None, 0)
    """
    if not valid_vt or not valid_rt:
        return None, 0

    def count_matches(offset_s, tolerance_s=default_tol):
        offset = datetime.timedelta(seconds=offset_s)
        tolerance = datetime.timedelta(seconds=tolerance_s)
        count = 0
        total_err = 0.0
        for _vi, vtime in valid_vt:
            adjusted = (vtime - offset).replace(tzinfo=None)
            for _ri, rs, re_ in valid_rt:
                if adjusted >= rs - tolerance and adjusted <= re_ + tolerance:
                    count += 1
                    mid = rs + (re_ - rs) / 2
                    total_err += abs((adjusted - mid).total_seconds())
                    break
        return count, total_err

    # Pass 1: Coarse search -24h..+24h in 15-second steps
    best_offset_s = 0
    best_count = 0
    for offset_s in range(-86400, 86400, 15):
        count, _ = count_matches(offset_s)
        if count > best_count:
            best_count = count
            best_offset_s = offset_s

    # Pass 2: Fine search +/-15s around best, in 1-second steps
    fine_best_s = best_offset_s
    fine_best_count = best_count
    fine_best_err = float("inf")
    for offset_s in range(best_offset_s - 15, best_offset_s + 16):
        count, err = count_matches(offset_s)
        if count > fine_best_count or (
            count == fine_best_count and err < fine_best_err
        ):
            fine_best_count = count
            fine_best_s = offset_s
            fine_best_err = err

    # Pass 3: Sub-second refinement +/-1s in 0.1s steps
    ss_best_sec = float(fine_best_s)
    ss_best_count = fine_best_count
    ss_best_err = fine_best_err
    for ds in range(-10, 11):
        offset_sec = fine_best_s + ds / 10.0
        count, err = count_matches(offset_sec)
        if count > ss_best_count or (
            count == ss_best_count and err < ss_best_err
        ):
            ss_best_count = count
            ss_best_sec = offset_sec
            ss_best_err = err

    return datetime.timedelta(seconds=ss_best_sec), ss_best_count


# ---------------------------------------------------------------------------
# Clock offset calibration
# ---------------------------------------------------------------------------

def calibrate_clock_offset(
    video_times: List[Optional[datetime.datetime]],
    recorder_times: List[Tuple[Optional[datetime.datetime],
                               Optional[datetime.datetime]]],
    tolerance_sec: float = 15,
) -> Tuple[Optional[datetime.timedelta], int]:
    """Find the clock offset that maximizes video-recorder temporal overlap.

    Cameras and external recorders often have different internal clocks.
    This function performs a three-pass search to find the offset (in
    seconds) that, when subtracted from video creation times, best
    aligns them with recorder time ranges.

    Parameters
    ----------
    video_times : list
        Datetime objects (or None) per video.
    recorder_times : list
        (start_dt, end_dt) tuples per recorder group.
    tolerance_sec : float
        Tolerance for matching (default 15).

    Returns
    -------
    (timedelta_offset, match_count) or (None, 0)
    """
    valid_vt = [(i, t) for i, t in enumerate(video_times) if t is not None]
    valid_rt = [
        (i, s, e)
        for i, (s, e) in enumerate(recorder_times)
        if s and e
    ]
    return _three_pass_search(valid_vt, valid_rt, tolerance_sec)


def calibrate_subset(
    indexed_vtimes: List[tuple],
    indexed_rtimes: List[tuple],
    tolerance_sec: float = 15,
) -> Tuple[Optional[datetime.timedelta], int]:
    """Calibrate clock offset for a subset of videos and recorders.

    Like calibrate_clock_offset but takes pre-indexed lists:
        indexed_vtimes: [(vi, datetime_or_None), ...]
        indexed_rtimes: [(ri, (start_dt, end_dt)), ...]

    Parameters
    ----------
    indexed_vtimes : list
        Pre-indexed video timestamps.
    indexed_rtimes : list
        Pre-indexed recorder time ranges.
    tolerance_sec : float
        Tolerance for matching (default 15).

    Returns
    -------
    (timedelta_offset, match_count) or (None, 0)
    """
    valid_vt = [(vi, t) for vi, t in indexed_vtimes if t is not None]
    valid_rt = [
        (ri, s, e) for ri, (s, e) in indexed_rtimes if s and e
    ]
    return _three_pass_search(valid_vt, valid_rt, tolerance_sec)


# ---------------------------------------------------------------------------
# Timestamp-based assignment
# ---------------------------------------------------------------------------

def timestamp_assign(
    video_times: List[Optional[datetime.datetime]],
    recorder_times: List[Tuple[Optional[datetime.datetime],
                               Optional[datetime.datetime]]],
    clock_offset: Optional[datetime.timedelta],
    tolerance_sec: float = 15,
    recorder_durations: Optional[List[float]] = None,
) -> Dict[int, Tuple[int, float]]:
    """Pre-assign each video to a recorder group based on timestamp overlap.

    Using the calibrated clock offset, maps each video to the recorder
    group whose time range contains the video's adjusted creation time.

    Parameters
    ----------
    video_times : list
        Datetime objects (or None) per video.
    recorder_times : list
        (start_dt, end_dt) tuples per recorder group.
    clock_offset : timedelta or None
        From calibrate_clock_offset(). Returns empty dict if None.
    tolerance_sec : float
        Tolerance in seconds (default 15).
    recorder_durations : list of float, optional
        Actual audio durations per recorder for sanity check.

    Returns
    -------
    dict
        Mapping video_index -> (recorder_index, predicted_offset_seconds).
    """
    if clock_offset is None:
        return {}
    assignments = {}
    tolerance = datetime.timedelta(seconds=tolerance_sec)
    for vi, vtime in enumerate(video_times):
        if vtime is None:
            continue
        adjusted = (vtime - clock_offset).replace(tzinfo=None)
        for ri, (rs, re_) in enumerate(recorder_times):
            if rs and re_ and adjusted >= rs - tolerance and adjusted <= re_ + tolerance:
                offset_in_recorder = (adjusted - rs).total_seconds()
                # Sanity check: predicted offset must not exceed actual
                # audio duration (filesystem time range can be misleading)
                if recorder_durations and ri < len(recorder_durations):
                    rdur = recorder_durations[ri]
                    if rdur > 0 and offset_in_recorder > rdur + tolerance_sec:
                        continue  # try next recorder
                assignments[vi] = (ri, offset_in_recorder)
                break
    return assignments


def timestamp_assign_subset(
    v_indices: List[int],
    video_times: List[Optional[datetime.datetime]],
    a_indices: List[int],
    recorder_times: List[Tuple[Optional[datetime.datetime],
                               Optional[datetime.datetime]]],
    clock_offset: Optional[datetime.timedelta],
    tolerance_sec: float = 15,
    recorder_durations: Optional[List[float]] = None,
) -> Dict[int, Tuple[int, float]]:
    """Assign a subset of videos to a subset of recorders using clock offset.

    Parameters
    ----------
    v_indices : list of int
        Video indices to consider.
    video_times : list
        Full list of video times (indexed by vi).
    a_indices : list of int
        Recorder indices to consider.
    recorder_times : list
        Full list of recorder times (indexed by ri).
    clock_offset : timedelta or None
        From calibration.
    tolerance_sec : float
        Tolerance in seconds (default 15).
    recorder_durations : list of float, optional
        Actual audio durations per recorder.

    Returns
    -------
    dict
        Mapping video_index -> (recorder_index, predicted_offset_seconds).
    """
    if clock_offset is None:
        return {}
    assignments = {}
    tolerance = datetime.timedelta(seconds=tolerance_sec)
    for vi in v_indices:
        vtime = video_times[vi]
        if vtime is None:
            continue
        adjusted = (vtime - clock_offset).replace(tzinfo=None)
        for ri in a_indices:
            rs, re_ = recorder_times[ri]
            if rs and re_ and adjusted >= rs - tolerance and adjusted <= re_ + tolerance:
                offset_in_recorder = (adjusted - rs).total_seconds()
                # Sanity check: reject if offset exceeds actual audio duration
                if recorder_durations and ri < len(recorder_durations):
                    rdur = recorder_durations[ri]
                    if rdur > 0 and offset_in_recorder > rdur + tolerance_sec:
                        continue
                assignments[vi] = (ri, offset_in_recorder)
                break
    return assignments
