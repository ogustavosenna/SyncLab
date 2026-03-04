"""
Background sync runner — executes the matching pipeline in a thread.

Extracted from server.py during v1.3.0 refactoring.
Handles progress event transformation from matcher format to
frontend WebSocket format.

Dependencies: Flask-SocketIO, SyncEngine, SmartMatcher.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict

from synclab.core.engine import SyncEngine
from synclab.core.matcher import SmartMatcher
from synclab.app.helpers import (
    compute_confidence_badge,
    serialize_timeline,
)


def run_sync(
    state: Dict[str, Any],
    socketio: Any,
    output_dir: str,
) -> None:
    """Run synchronization in a background thread.

    Creates a SyncEngine and SmartMatcher, runs the full matching
    pipeline, transforms progress events for the frontend, and
    stores results in ``state["results"]``.

    Parameters
    ----------
    state : dict
        Shared application state with keys: ``config``, ``videos``,
        ``audio_groups``, ``syncing``, ``results``.
    socketio : SocketIO
        Flask-SocketIO instance for emitting progress events.
    output_dir : str
        Output directory path (currently unused but reserved).
    """
    temp_dir = None
    try:
        config = state["config"]
        videos = state["videos"]
        audio_groups = state["audio_groups"]

        total_videos = len(videos)
        total_audio = len(audio_groups)

        # Create temp directory
        temp_dir = Path(tempfile.mkdtemp(prefix="synclab_"))

        # Phase weight ranges for overall progress bar
        # Metadata + timestamp are fast (~4%), audio sync is the real work (~88%)
        PHASE_RANGES = {
            "metadata": (0, 2),
            "timestamp_calibration": (2, 4),
            "audio_sync": (4, 92),
            "brute_force": (92, 99),
            "done": (100, 100),
        }

        def emit_progress(event_type, data):
            """Transform matcher events into frontend-compatible format.

            OVERALL progress: steady climb from 0-100% across all phases.
            STEP progress: per-video indicator (frontend animates per-video
            cycling so the two bars show clearly different information).
            """
            if not state["syncing"]:
                raise InterruptedError("Sync cancelled by user")

            if event_type == "phase":
                # Matcher uses "name", frontend expects "phase"
                phase_name = data.get("name", "")
                low, _ = PHASE_RANGES.get(phase_name, (0, 0))
                socketio.emit("phase", {
                    "phase": phase_name,
                    "description": data.get("description", ""),
                    "percent": low,
                })

            elif event_type == "progress":
                vi = data.get("video_index", -1)
                phase = data.get("phase", "audio_sync")
                detail = data.get("detail", "")

                # --- OVERALL: phase-weighted total progress ---
                low, high = PHASE_RANGES.get(phase, (0, 100))

                if phase == "brute_force":
                    # Use brute-force-local index for accurate progress
                    bf_idx = data.get("bf_index", 0)
                    bf_total = data.get("bf_total", 1)
                    if bf_total > 0:
                        overall_pct = low + (high - low) * (bf_idx + 1) / bf_total
                    else:
                        overall_pct = low
                    overall_detail = (
                        f"Extra pass — Unassigned video "
                        f"{bf_idx + 1} of {bf_total}"
                    )
                elif vi >= 0 and total_videos > 0:
                    video_pct = (vi + 1) / total_videos
                    overall_pct = low + (high - low) * video_pct
                    if phase == "audio_sync":
                        overall_detail = (
                            f"Phase 3 — Video {vi + 1} of {total_videos}"
                        )
                    else:
                        overall_detail = detail
                else:
                    overall_pct = low
                    # Show phase name for metadata/timestamp phases
                    if phase == "metadata":
                        overall_detail = f"Phase 1 — {detail}"
                    elif phase == "timestamp_calibration":
                        overall_detail = f"Phase 2 — {detail}"
                    else:
                        overall_detail = detail

                # --- STEP: clean detail for per-video display ---
                # Remove "[X/Y] " prefix from matcher detail strings
                step_detail = detail
                if detail.startswith("[") and "] " in detail:
                    step_detail = detail.split("] ", 1)[1]

                socketio.emit("progress", {
                    "overall_percent": round(overall_pct),
                    "overall_detail": overall_detail,
                    "video_index": vi,
                    "step_detail": step_detail,
                })

            elif event_type == "match":
                # Add type field and normalize field names for frontend
                match_data = dict(data)
                method = match_data.get("method", "")
                conf = match_data.get("confidence", 0)
                if conf > 0 or "timestamp" in method:
                    match_data["type"] = "synced"
                else:
                    match_data["type"] = "video_only"
                match_data["video_name"] = match_data.pop("video", "")
                match_data["audio_name"] = match_data.pop("audio", "")
                # Add confidence badge (v1.1)
                match_data["badge"] = compute_confidence_badge(match_data)
                socketio.emit("match", match_data)

            else:
                socketio.emit(event_type, data)

        # Emit start
        emit_progress("sync_started", {
            "total_videos": total_videos,
            "total_audio_groups": total_audio,
        })

        # Create engine and matcher
        engine = SyncEngine(config)
        matcher = SmartMatcher(engine, config)

        # Run matching
        timeline = matcher.match(
            videos,
            audio_groups,
            temp_dir,
            progress_callback=emit_progress,
        )

        # Count results
        synced_audio = sum(
            1 for item in timeline
            if item.get("type") == "synced"
            and item.get("method", "") != "timestamp_only"
        )
        synced_ts = sum(
            1 for item in timeline
            if item.get("type") == "synced"
            and item.get("method", "") == "timestamp_only"
        )
        video_only = sum(1 for item in timeline if item.get("type") == "video_only")
        audio_only = sum(1 for item in timeline if item.get("type") == "audio_only")

        # Serialize timeline for JSON
        serialized = serialize_timeline(timeline)

        results = {
            "timeline": timeline,
            "summary": {
                "total_videos": total_videos,
                "total_audio_groups": total_audio,
                "synced_audio": synced_audio,
                "synced_timestamp": synced_ts,
                "video_only": video_only,
                "audio_only": audio_only,
            },
            "serialized": serialized,
        }

        state["results"] = results

        emit_progress("sync_complete", results["summary"])

    except InterruptedError:
        socketio.emit("sync_cancelled", {})
    except Exception as e:
        socketio.emit("sync_error", {"message": str(e)})
    finally:
        state["syncing"] = False
        # Clean up temp directory
        if temp_dir is not None:
            try:
                if temp_dir.exists():
                    shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception:
                pass
