"""
Media metadata extraction — timestamps and WAV classification.

Extracted from matcher.py during v1.3.0 refactoring.
Provides functions for extracting creation times from video files,
time ranges from recorder WAV files, and classifying WAV files
by track type.

Dependencies: subprocess, json, datetime, pathlib.
"""

from __future__ import annotations

import json
import datetime
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from synclab.core.audio import classify_track
from synclab.core.media import get_media_info


# ---------------------------------------------------------------------------
# Video creation time
# ---------------------------------------------------------------------------

def get_video_creation_time(video_path: Path) -> Optional[datetime.datetime]:
    """Extract creation_time from video metadata, with filesystem fallback.

    Priority:
      1. ffprobe creation_time tag (most reliable, embedded in container)
      2. Filesystem creation time (st_ctime on Windows)
      3. Filesystem modification time (st_mtime, last resort)

    Parameters
    ----------
    video_path : Path
        Path to the video file.

    Returns
    -------
    datetime or None
        Creation datetime, or None if unavailable.
    """
    # Priority 1: ffprobe creation_time tag
    try:
        from synclab.subprocess_utils import subprocess_hide_window, get_ffprobe
        r = subprocess.run(
            [
                get_ffprobe(), "-v", "quiet",
                "-print_format", "json",
                "-show_format", str(video_path),
            ],
            capture_output=True, text=True, timeout=15,
            **subprocess_hide_window(),
        )
        data = json.loads(r.stdout)
        ct = data.get("format", {}).get("tags", {}).get("creation_time", "")
        if ct:
            return datetime.datetime.fromisoformat(
                ct.replace("Z", "+00:00")
            )
    except Exception:
        pass

    # Priority 2-3: Filesystem timestamps
    try:
        stat = Path(video_path).stat()
        # On Windows, st_ctime is file creation time
        ts = stat.st_ctime
        if ts and ts > 0:
            return datetime.datetime.fromtimestamp(ts)
        # Last resort: modification time
        ts = stat.st_mtime
        if ts and ts > 0:
            return datetime.datetime.fromtimestamp(ts)
    except Exception:
        pass

    return None


# ---------------------------------------------------------------------------
# Recorder time range
# ---------------------------------------------------------------------------

def get_recorder_time_range(
    audio_group: dict,
) -> Tuple[Optional[datetime.datetime], Optional[datetime.datetime]]:
    """Get start/end times from recorder WAV file timestamps.

    Prefers the main stereo mix file (containing '_LR' in the name)
    for the most representative timestamps.  Falls back to the first
    WAV file in the group.

    Parameters
    ----------
    audio_group : dict
        Dict with ``wav_files`` key (list of Path objects).

    Returns
    -------
    (start_datetime, end_datetime) or (None, None)
    """
    # Prefer the stereo mix file for representative timestamps
    for wav_path in audio_group["wav_files"]:
        if "_LR" in wav_path.name.upper():
            stat = wav_path.stat()
            return (
                datetime.datetime.fromtimestamp(stat.st_ctime),
                datetime.datetime.fromtimestamp(stat.st_mtime),
            )
    # Fallback to first WAV file
    if audio_group["wav_files"]:
        stat = audio_group["wav_files"][0].stat()
        return (
            datetime.datetime.fromtimestamp(stat.st_ctime),
            datetime.datetime.fromtimestamp(stat.st_mtime),
        )
    return (None, None)


# ---------------------------------------------------------------------------
# WAV file classification
# ---------------------------------------------------------------------------

def classify_wav_files(
    wav_files: List[Path],
    track_types: List[str],
) -> Dict[str, dict]:
    """Classify WAV files by track type and get media info for each.

    Parameters
    ----------
    wav_files : list of Path
        WAV files to classify.
    track_types : list of str
        Track type identifiers (e.g. ``["_Tr1", "_LR"]``).

    Returns
    -------
    dict
        Mapping track_type string -> media info dict.
    """
    result = {}
    for w in wav_files:
        tt = classify_track(w, track_types)
        result[tt] = get_media_info(w)
    return result
