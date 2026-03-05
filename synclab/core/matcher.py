"""
SyncLab Core - Smart Matcher
Timestamp-first audio-video matching with multi-stage cross-correlation.

Algorithm overview:
  Phase 1: Collect media metadata (video info, audio track classification)
  Phase 2: Calibrate clock offset between camera and recorder clocks
           using a 3-pass search (coarse 15s -> fine 1s -> sub-second 0.1s)
  Phase 3: Audio cross-correlation with timestamp-guided search window
  Phase 3b: Brute-force audio scan for videos without timestamp assignment

The matcher accepts a generic project structure: a list of video dicts
and a list of audio group dicts. No assumptions are made about folder
naming conventions or project-specific layout.

v1.3.0 refactoring:
  - Metadata extraction moved to metadata.py
  - Clock calibration and assignment moved to calibration.py
  - Timeline construction moved to timeline.py
  - SmartMatcher remains the orchestrator with thin wrappers
    for backward compatibility.
"""

import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from synclab.core.audio import format_duration, safe_remove

logger = logging.getLogger(__name__)
from synclab.core.media import get_media_info
from synclab.core.metadata import (
    get_video_creation_time,
    get_recorder_time_range,
    classify_wav_files,
)
from synclab.core.calibration import (
    valid_offset,
    calibrate_clock_offset,
    calibrate_subset,
    timestamp_assign,
    timestamp_assign_subset,
)
from synclab.core.timeline import (
    audio_only_item,
    video_only_item,
    build_timeline,
)


def _brute_force_one_video(
    engine, vi, video, video_info, audio_groups,
    recorder_durations, temp_dir, config, multi_source,
):
    """Process one unmatched video against all audio groups.

    Designed to run in a ThreadPoolExecutor.  Thread-safe because:
    - engine._zoom_cache is protected by _zoom_lock
    - Each video gets its own cam_wav temp file (unique per video path)
    - All other inputs are read-only

    Returns a dict with match results, or a no-match diagnostic dict.
    """
    vdur = video_info.get("duration", 0.0)
    nr = len(audio_groups)
    threshold = config.get("threshold", 0.05)
    min_pr = config.get("min_peak_ratio", 2.0)
    v_src = video.get("source_folder", "")

    cam_wav = engine.prepare_camera_audio(
        video["path"], temp_dir, vdur,
    )
    if cam_wav is None:
        return {
            "vi": vi, "matched": False,
            "diagnostics": {
                "video_name": video["path"].name,
                "method_used": "no_camera_audio",
            },
        }

    try:
        best_ri = -1
        best_result = {
            "offset": 0.0, "confidence": 0.0,
            "method": "none", "level": 0,
        }
        best_same_ri = -1
        best_same_result = dict(best_result)
        bf_stage_records = []

        for ri in range(nr):
            rdur = recorder_durations[ri]
            if vdur > 10 and rdur < vdur * 0.5:
                continue

            ag = audio_groups[ri]

            result = engine.sync_with_zoom(
                cam_wav, ag["wav_files"], temp_dir,
                suffix=f"r{ri}_bf{vi}",
            )

            bf_diag = result.pop("diagnostics", {})
            bf_stage_records.append({
                "audio_group": ag["zoom_dir"].name,
                "ri": ri,
                "offset": result.get("offset", 0.0),
                "confidence": result.get("confidence", 0.0),
                "peak_ratio": result.get("peak_ratio"),
                "method": result.get("method", "?"),
                "stages": bf_diag.get("stages", []),
                "speech_ratio": bf_diag.get("speech_ratio"),
            })

            conf = result.get("confidence", 0.0)
            off = result.get("offset", 0.0)

            max_cam = config.get("max_camera_sec", 60)
            if conf > 0 and not valid_offset(off, vdur, rdur, max_cam):
                continue

            if conf > best_result.get("confidence", 0.0):
                best_ri = ri
                best_result = result

            a_src = ag.get("source_folder", "")
            if a_src == v_src and conf > best_same_result.get("confidence", 0.0):
                best_same_ri = ri
                best_same_result = result

        # Source-folder preference
        chosen_ri = best_ri
        chosen_result = best_result
        if (
            multi_source
            and best_same_ri >= 0
            and best_same_result.get("confidence", 0.0) >= threshold
        ):
            chosen_ri = best_same_ri
            chosen_result = best_same_result

        bf_pr = chosen_result.get("peak_ratio") or 0.0
        bf_conf = chosen_result.get("confidence", 0.0)

        if (
            chosen_ri >= 0
            and bf_conf >= threshold
            and (bf_pr <= 0 or bf_pr >= min_pr)
        ):
            ag = audio_groups[chosen_ri]
            return {
                "vi": vi,
                "matched": True,
                "ri": chosen_ri,
                "result": chosen_result,
                "diagnostics": {
                    "video_name": video["path"].name,
                    "audio_group": ag["zoom_dir"].name,
                    "video_duration": vdur,
                    "recorder_duration": recorder_durations[chosen_ri],
                    "method_used": f"brute_force:{chosen_result.get('method', '?')}",
                    "confidence": bf_conf,
                    "peak_ratio": bf_pr,
                    "predicted_offset": None,
                    "brute_force_candidates": bf_stage_records,
                },
            }
        else:
            why = "brute_force_no_match"
            if chosen_ri >= 0 and bf_conf >= threshold and bf_pr > 0 and bf_pr < min_pr:
                why = f"brute_force_weak_peak ({bf_pr:.2f} < {min_pr})"
            return {
                "vi": vi,
                "matched": False,
                "diagnostics": {
                    "video_name": video["path"].name,
                    "audio_group": None,
                    "video_duration": vdur,
                    "method_used": "no_match",
                    "confidence": bf_conf,
                    "peak_ratio": bf_pr,
                    "why_fallback": why,
                    "brute_force_candidates": bf_stage_records,
                },
            }
    finally:
        safe_remove(cam_wav)


class SmartMatcher:
    """Timestamp-first matching with multi-stage audio cross-correlation.

    Workflow:
      1. Extract creation_time from video metadata and file timestamps
         from recorder WAV files.
      2. Calibrate the clock offset between camera and recorder
         automatically via 3-pass search.
      3. Pre-assign each video to a recorder group by temporal overlap.
      4. Run audio cross-correlation within the identified recorder group
         using a narrow search window (default +/-30s).
      5. Fall back to brute-force audio search for unmatched videos.

    The confidence threshold can be very low (default 5%) because the
    timestamp pre-assignment already identifies the correct recorder group.

    Args:
        engine: A SyncEngine instance that provides audio sync methods.
        config: Configuration dict with matching parameters.
    """

    def __init__(self, engine, config):
        self.engine = engine
        self.config = config

    # ------------------------------------------------------------------
    # Wrappers — delegate to module-level functions
    #
    # These maintain backward compatibility so that tests and any
    # external code calling self._method() continue to work.
    # ------------------------------------------------------------------

    def _valid_offset(self, offset, video_duration, recorder_duration):
        """Check whether offset places the video within the recorder range."""
        max_cam = self.config.get("max_camera_sec", 60)
        return valid_offset(offset, video_duration, recorder_duration, max_cam)

    def _get_video_creation_time(self, video_path):
        """Extract creation_time from video metadata."""
        return get_video_creation_time(video_path)

    def _get_recorder_time_range(self, audio_group):
        """Get start/end times from recorder WAV file timestamps."""
        return get_recorder_time_range(audio_group)

    def _calibrate_clock_offset(self, video_times, recorder_times):
        """Find the clock offset that maximizes temporal overlap."""
        tol = self.config.get("timestamp_tolerance_sec", 15)
        return calibrate_clock_offset(video_times, recorder_times, tol)

    def _calibrate_subset(self, indexed_vtimes, indexed_rtimes):
        """Calibrate clock offset for a subset of videos and recorders."""
        tol = self.config.get("timestamp_tolerance_sec", 15)
        return calibrate_subset(indexed_vtimes, indexed_rtimes, tol)

    def _timestamp_assign(self, video_times, recorder_times, clock_offset,
                          recorder_durations=None):
        """Pre-assign videos to recorders based on timestamp overlap."""
        tol = self.config.get("timestamp_tolerance_sec", 15)
        return timestamp_assign(
            video_times, recorder_times, clock_offset,
            tol, recorder_durations,
        )

    def _timestamp_assign_subset(
        self, v_indices, video_times, a_indices, recorder_times, clock_offset,
        recorder_durations=None,
    ):
        """Assign a subset of videos to a subset of recorders."""
        tol = self.config.get("timestamp_tolerance_sec", 15)
        return timestamp_assign_subset(
            v_indices, video_times, a_indices, recorder_times,
            clock_offset, tol, recorder_durations,
        )

    def _classify_wav_files(self, wav_files):
        """Classify WAV files by track type and get media info."""
        track_types = self.config.get("track_types", [])
        return classify_wav_files(wav_files, track_types)

    def _audio_only_item(self, wav_by_type, audio_name, group_id):
        """Build a timeline item for an unmatched recorder group."""
        return audio_only_item(wav_by_type, audio_name, group_id)

    def _video_only_item(self, video_info, video_name, group_id):
        """Build a timeline item for an unmatched video."""
        return video_only_item(video_info, video_name, group_id)

    def _build_timeline(self, videos, audio_groups, video_infos,
                        audio_wav_by_type, matched_pairs, matched_recorders,
                        recorder_durations, ts_assignments,
                        video_diagnostics=None):
        """Build a sorted timeline from matching results."""
        return build_timeline(
            videos, audio_groups, video_infos, audio_wav_by_type,
            matched_pairs, matched_recorders, recorder_durations,
            ts_assignments, video_diagnostics,
        )

    # ------------------------------------------------------------------
    # Main matching entry point
    # ------------------------------------------------------------------

    def match(self, videos, audio_groups, temp_dir, progress_callback=None):
        """Run the full matching pipeline.

        This is the main entry point. It executes all phases in sequence:
        metadata collection, timestamp calibration, audio cross-correlation,
        and brute-force fallback.

        Args:
            videos: List of dicts, each with at least:
                - 'path': Path object to the video file.
                - 'card': (optional) source card/group identifier.
            audio_groups: List of dicts, each with:
                - 'zoom_dir': Path to the recorder directory.
                - 'wav_files': List of Path objects for WAV files.
                - 'card': (optional) source card/group identifier.
            temp_dir: Path to a temporary directory for intermediate files.
            progress_callback: Optional callable with signature
                callback(event_type: str, data: dict).
                event_type is one of "phase", "progress", "match", "info".

        Returns:
            List of timeline item dicts, each with keys:
                type, video_info, wav_by_type (or audio_info), offset,
                confidence, method, level, video_name, audio_name, card.
        """
        temp_dir = Path(temp_dir)
        nv = len(videos)
        nr = len(audio_groups)
        threshold = self.config.get("threshold", 0.05)
        window_sec = self.config.get("sync_window_sec", 30)

        def notify(event_type, data):
            if progress_callback is not None:
                progress_callback(event_type, data)

        notify("phase", {
            "name": "start",
            "description": "Sync (XCorr + Envelope)",
            "total_videos": nv,
            "total_audio_groups": nr,
        })

        # ==============================================================
        # Phase 1: Collect media metadata
        # ==============================================================
        notify("phase", {
            "name": "metadata",
            "description": f"Collecting media info ({nv} videos, {nr} audio groups)",
        })

        video_infos = []
        for v in videos:
            video_infos.append(get_media_info(v["path"]))
            notify("progress", {
                "phase": "metadata",
                "detail": f"Video: {v['path'].name}",
            })

        audio_wav_by_type = []
        recorder_durations = []
        for ag in audio_groups:
            wbt = self._classify_wav_files(ag["wav_files"])
            audio_wav_by_type.append(wbt)

            # Determine recorder duration from the best available track
            rdur = 0.0
            for tt in ["_Tr1", "_LR", "_Other"]:
                if tt in wbt:
                    rdur = wbt[tt].get("duration", 0.0)
                    break
            if rdur == 0.0 and wbt:
                rdur = list(wbt.values())[0].get("duration", 0.0)
            recorder_durations.append(rdur)

            notify("progress", {
                "phase": "metadata",
                "detail": f"Audio: {ag['zoom_dir'].name} ({format_duration(rdur)})",
            })

        # ==============================================================
        # Phase 2: Timestamp calibration
        # ==============================================================
        notify("phase", {
            "name": "timestamp_calibration",
            "description": "Calibrating clock offset from timestamps",
        })

        video_times = [
            self._get_video_creation_time(v["path"]) for v in videos
        ]
        recorder_times = [
            self._get_recorder_time_range(ag) for ag in audio_groups
        ]

        # Detect if we have multiple audio source folders.
        # When multiple audio folders are loaded (e.g., day2 + day3), ZOOM
        # groups from different days may share names (ZOOM0001, ZOOM0002...).
        # A single global calibration would cross-match between days.
        # Instead, calibrate per source-folder group for best results.
        audio_sources = list(dict.fromkeys(
            ag.get("source_folder", "") for ag in audio_groups
        ))
        video_sources = list(dict.fromkeys(
            v.get("source_folder", "") for v in videos
        ))
        multi_source = len(audio_sources) > 1 and len(video_sources) > 1

        notify("info", {
            "message": (
                f"Source detection: {len(video_sources)} video sources "
                f"{video_sources}, {len(audio_sources)} audio sources "
                f"{audio_sources} → multi_source={multi_source}"
            ),
        })

        ts_assignments = {}

        if multi_source:
            # ---- Per-source-folder calibration ----
            # Group recorder indices by their source_folder
            audio_ri_by_source = {}
            for ri, ag in enumerate(audio_groups):
                src = ag.get("source_folder", "")
                audio_ri_by_source.setdefault(src, []).append(ri)

            # Group video indices by their source_folder
            video_vi_by_source = {}
            for vi, v in enumerate(videos):
                src = v.get("source_folder", "")
                video_vi_by_source.setdefault(src, []).append(vi)

            # For each video source, find the best audio source match
            for v_src, v_indices in video_vi_by_source.items():
                best_offset = None
                best_count = 0
                best_a_src = None

                for a_src, a_indices in audio_ri_by_source.items():
                    # Build subset lists for this pair
                    sub_vtimes = [(vi, video_times[vi]) for vi in v_indices]
                    sub_rtimes = [(ri, recorder_times[ri]) for ri in a_indices]

                    offset, count = self._calibrate_subset(
                        sub_vtimes, sub_rtimes,
                    )
                    if count > best_count:
                        best_count = count
                        best_offset = offset
                        best_a_src = a_src

                if best_offset is not None and best_a_src is not None:
                    # Assign videos from this source using the best offset
                    a_indices = audio_ri_by_source[best_a_src]
                    sub_assign = self._timestamp_assign_subset(
                        v_indices, video_times,
                        a_indices, recorder_times,
                        best_offset,
                        recorder_durations=recorder_durations,
                    )
                    ts_assignments.update(sub_assign)

                    total_secs = int(best_offset.total_seconds())
                    h = total_secs // 3600
                    m = (total_secs % 3600) // 60
                    s = total_secs % 60
                    notify("info", {
                        "message": (
                            f"[{v_src} → {best_a_src}] "
                            f"Clock offset: {h}h{m:02d}m{s:02d}s | "
                            f"{best_count} videos pre-matched"
                        ),
                    })
        else:
            # ---- Single-source calibration (original logic) ----
            clock_offset, ts_count = self._calibrate_clock_offset(
                video_times, recorder_times,
            )
            if clock_offset is not None:
                ts_assignments = self._timestamp_assign(
                    video_times, recorder_times, clock_offset,
                    recorder_durations=recorder_durations,
                )
                total_secs = int(clock_offset.total_seconds())
                h = total_secs // 3600
                m = (total_secs % 3600) // 60
                s = total_secs % 60
                notify("info", {
                    "message": (
                        f"Clock offset: {h}h{m:02d}m{s:02d}s | "
                        f"{ts_count}/{nv} videos pre-matched by timestamp"
                    ),
                })
            else:
                notify("info", {
                    "message": "Timestamps not available - using audio-only search",
                })

        n_ts = len(ts_assignments)
        n_no_ts = nv - n_ts

        # Adaptive tolerance: if fewer than 50% assigned, retry with wider window
        tol_max = self.config.get("timestamp_tolerance_max", 30)
        tol_default = self.config.get("timestamp_tolerance_sec", 15)
        if n_ts < nv * 0.5 and tol_max > tol_default and nv > 0:
            notify("info", {
                "message": (
                    f"Only {n_ts}/{nv} assigned with {tol_default}s tolerance — "
                    f"retrying with {tol_max}s"
                ),
            })
            # Temporarily widen tolerance and re-run assignment
            saved_tol = self.config.get("timestamp_tolerance_sec", 15)
            self.config["timestamp_tolerance_sec"] = tol_max
            try:
                if multi_source:
                    ts_assignments_wide = {}
                    for v_src, v_indices in video_vi_by_source.items():
                        best_offset = None
                        best_count = 0
                        best_a_src = None
                        for a_src, a_indices in audio_ri_by_source.items():
                            sub_vtimes = [(vi, video_times[vi]) for vi in v_indices]
                            sub_rtimes = [(ri, recorder_times[ri]) for ri in a_indices]
                            offset, count = self._calibrate_subset(
                                sub_vtimes, sub_rtimes,
                            )
                            if count > best_count:
                                best_count = count
                                best_offset = offset
                                best_a_src = a_src
                        if best_offset is not None and best_a_src is not None:
                            a_indices = audio_ri_by_source[best_a_src]
                            sub_assign = self._timestamp_assign_subset(
                                v_indices, video_times,
                                a_indices, recorder_times,
                                best_offset,
                                recorder_durations=recorder_durations,
                            )
                            ts_assignments_wide.update(sub_assign)
                else:
                    clock_offset_wide, _ = self._calibrate_clock_offset(
                        video_times, recorder_times,
                    )
                    ts_assignments_wide = self._timestamp_assign(
                        video_times, recorder_times, clock_offset_wide,
                        recorder_durations=recorder_durations,
                    ) if clock_offset_wide else {}

                # Only use wider results for videos that weren't already assigned
                new_count = 0
                for vi, assignment in ts_assignments_wide.items():
                    if vi not in ts_assignments:
                        ts_assignments[vi] = assignment
                        new_count += 1
                if new_count > 0:
                    notify("info", {
                        "message": (
                            f"Wider tolerance recovered {new_count} additional assignments"
                        ),
                    })
            finally:
                self.config["timestamp_tolerance_sec"] = saved_tol

            n_ts = len(ts_assignments)
            n_no_ts = nv - n_ts

        notify("info", {
            "message": (
                f"Assignments: {n_ts} by timestamp, {n_no_ts} without recorder"
            ),
        })

        # ==============================================================
        # Phase 3: Audio cross-correlation (timestamp-guided)
        # ==============================================================
        notify("phase", {
            "name": "audio_sync",
            "description": (
                f"Audio cross-correlation (window +/-{window_sec}s)"
            ),
        })

        matched_pairs = {}      # vi -> (ri, sync_data)
        matched_recorders = set()
        total_synced_audio = 0
        video_diagnostics = {}  # vi -> diagnostic dict (v1.1)

        for vi in range(nv):
            v = videos[vi]
            v_info = video_infos[vi]
            vdur = v_info.get("duration", 0.0)

            # Skip videos without camera audio
            if not v_info.get("has_audio"):
                notify("progress", {
                    "phase": "audio_sync",
                    "video_index": vi,
                    "detail": f"[{vi+1}/{nv}] {v['path'].stem[:15]}: no camera audio",
                })
                continue

            # Skip videos not assigned to any recorder by timestamp
            if vi not in ts_assignments:
                notify("progress", {
                    "phase": "audio_sync",
                    "video_index": vi,
                    "detail": (
                        f"[{vi+1}/{nv}] {v['path'].stem[:15]}: "
                        f"no recorder assignment (timestamp)"
                    ),
                })
                notify("info", {
                    "message": (
                        f"{v['path'].name} ({format_duration(vdur)}) "
                        f"-> No recorder by timestamp"
                    ),
                })
                continue

            ri, predicted_off = ts_assignments[vi]
            ag = audio_groups[ri]
            rdur = recorder_durations[ri]

            notify("info", {
                "message": (
                    f"{v['path'].name} ({format_duration(vdur)}) -> "
                    f"{ag['zoom_dir'].name} ({format_duration(rdur)}) "
                    f"ts_off={predicted_off:.0f}s"
                ),
            })

            # Extract camera audio to temporary WAV
            cam_wav = self.engine.prepare_camera_audio(
                v["path"], temp_dir, vdur,
            )
            if cam_wav is None:
                # No camera audio extractable -> accept timestamp-only match
                matched_pairs[vi] = (ri, {
                    "offset": max(0, predicted_off),
                    "confidence": 0.05,
                    "method": "timestamp_only",
                    "level": 0,
                })
                matched_recorders.add(ri)
                video_diagnostics[vi] = {
                    "video_name": v["path"].name,
                    "audio_group": ag["zoom_dir"].name,
                    "video_duration": vdur,
                    "recorder_duration": rdur,
                    "method_used": "timestamp_only",
                    "confidence": 0.05,
                    "why_fallback": "no_extractable_audio",
                    "predicted_offset": predicted_off,
                    "stages": [],
                }
                notify("progress", {
                    "phase": "audio_sync",
                    "video_index": vi,
                    "detail": (
                        f"[{vi+1}/{nv}] {v['path'].stem[:12]}: "
                        f"timestamp_only (no extractable audio)"
                    ),
                })
                notify("match", {
                    "video": v["path"].name,
                    "audio": ag["zoom_dir"].name,
                    "method": "timestamp_only",
                    "offset": predicted_off,
                    "confidence": 0.05,
                })
                continue

            try:
                desc = (
                    f"[{vi+1}/{nv}] {v['path'].stem[:12]} "
                    f"x {ag['zoom_dir'].name}"
                )
                notify("progress", {
                    "phase": "audio_sync",
                    "video_index": vi,
                    "detail": desc,
                })

                result = self.engine.sync_with_zoom(
                    cam_wav, ag["wav_files"], temp_dir,
                    suffix=f"r{ri}",
                    predicted_offset=predicted_off,
                )

                # -- Collect diagnostics (v1.1) --
                diag = result.pop("diagnostics", {})
                diag["video_name"] = v["path"].name
                diag["audio_group"] = ag["zoom_dir"].name
                diag["video_duration"] = vdur
                diag["recorder_duration"] = rdur
                video_diagnostics[vi] = diag

                # -- Handle VAD skip (v1.1) --
                if result.get("method") == "vad_skip":
                    ts_off = max(0, min(predicted_off, rdur))
                    matched_pairs[vi] = (ri, {
                        "offset": ts_off,
                        "confidence": 0.05,
                        "method": "timestamp_only",
                        "level": 0,
                    })
                    matched_recorders.add(ri)
                    diag["method_used"] = "timestamp_only_vad_skip"
                    notify("info", {
                        "message": (
                            f"{v['path'].name}: camera audio too weak "
                            f"(speech_ratio="
                            f"{result.get('speech_ratio', 0):.3f}), "
                            f"using timestamp"
                        ),
                    })
                    notify("match", {
                        "video": v["path"].name,
                        "audio": ag["zoom_dir"].name,
                        "method": "timestamp_vad_skip",
                        "offset": ts_off,
                        "confidence": 0.05,
                    })
                    continue

                conf = result.get("confidence", 0.0)
                off = result.get("offset", 0.0)

                # Validate that offset is within recorder bounds
                if conf > 0 and not self._valid_offset(off, vdur, rdur):
                    notify("info", {
                        "message": (
                            f"[WARNING] offset={off:.2f}s out of bounds, "
                            f"using timestamp"
                        ),
                    })
                    diag["why_fallback"] = f"offset_out_of_bounds ({off:.2f}s)"
                    conf = 0.0

                if conf >= threshold:
                    # Check peak_ratio quality — reject ambiguous matches
                    pr = result.get("peak_ratio") or 0.0
                    min_pr = self.config.get("min_peak_ratio", 2.0)
                    if pr > 0 and pr < min_pr:
                        # Ambiguous match — fall back to timestamp_only
                        ts_off = max(0, min(predicted_off, rdur))
                        matched_pairs[vi] = (ri, {
                            "offset": ts_off,
                            "confidence": 0.05,
                            "method": "timestamp_only",
                            "level": 0,
                        })
                        matched_recorders.add(ri)
                        diag["method_used"] = "timestamp_fallback_weak_peak"
                        diag["why_fallback"] = (
                            f"peak_ratio_below_minimum "
                            f"({pr:.2f} < {min_pr})"
                        )
                        notify("info", {
                            "message": (
                                f"{v['path'].name}: peak_ratio={pr:.2f} "
                                f"< {min_pr} — using timestamp offset"
                            ),
                        })
                        notify("match", {
                            "video": v["path"].name,
                            "audio": ag["zoom_dir"].name,
                            "method": "timestamp_only",
                            "offset": ts_off,
                            "confidence": 0.05,
                            "peak_ratio": pr,
                        })
                    else:
                        matched_pairs[vi] = (ri, result)
                        matched_recorders.add(ri)
                        total_synced_audio += 1
                        diag["method_used"] = result.get("method", "?")
                        notify("match", {
                            "video": v["path"].name,
                            "audio": ag["zoom_dir"].name,
                            "method": result.get("method", "?"),
                            "offset": off,
                            "confidence": conf,
                            "peak_ratio": result.get("peak_ratio"),
                        })
                else:
                    # Fallback: use timestamp-derived offset
                    ts_off = max(0, min(predicted_off, rdur))
                    matched_pairs[vi] = (ri, {
                        "offset": ts_off,
                        "confidence": 0.05,
                        "method": "timestamp_only",
                        "level": 0,
                    })
                    matched_recorders.add(ri)
                    diag["method_used"] = "timestamp_fallback"
                    if not diag.get("why_fallback"):
                        diag["why_fallback"] = (
                            f"confidence_below_threshold "
                            f"({conf:.4f} < {threshold})"
                        )
                    notify("match", {
                        "video": v["path"].name,
                        "audio": ag["zoom_dir"].name,
                        "method": "timestamp_fallback",
                        "offset": ts_off,
                        "confidence": 0.05,
                        "audio_confidence": conf,
                    })

            finally:
                safe_remove(cam_wav)

        # ==============================================================
        # Phase 3b: Brute-force audio for videos without recorder
        # ==============================================================
        unmatched_vis = [
            vi for vi in range(nv)
            if vi not in ts_assignments
            and vi not in matched_pairs
            and video_infos[vi].get("has_audio")
        ]

        if unmatched_vis:
            n_bf = len(unmatched_vis)

            # Pre-warm zoom cache to avoid lock contention during
            # parallel execution (all ffmpeg extractions happen here,
            # once, sequentially).
            self.engine.warm_zoom_cache(audio_groups, temp_dir)

            max_workers = min(n_bf, max(1, (os.cpu_count() or 4) - 2))
            notify("phase", {
                "name": "brute_force",
                "description": (
                    f"Audio brute-force for {n_bf} "
                    f"unmatched videos ({max_workers} threads)"
                ),
                "bf_total": n_bf,
            })

            # Dispatch parallel workers
            futures = {}
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                for bf_idx, vi in enumerate(unmatched_vis):
                    v = videos[vi]
                    notify("info", {
                        "message": (
                            f"{v['path'].name} "
                            f"({format_duration(video_infos[vi].get('duration', 0))}) "
                            f"-> queued for brute-force"
                        ),
                    })
                    future = pool.submit(
                        _brute_force_one_video,
                        engine=self.engine,
                        vi=vi,
                        video=v,
                        video_info=video_infos[vi],
                        audio_groups=audio_groups,
                        recorder_durations=recorder_durations,
                        temp_dir=temp_dir,
                        config=self.config,
                        multi_source=multi_source,
                    )
                    futures[future] = (bf_idx, vi)

                # Collect results as they complete
                for done_count, future in enumerate(as_completed(futures)):
                    bf_idx, vi = futures[future]
                    try:
                        bf_result = future.result()
                    except Exception as exc:
                        logger.warning(
                            "Brute-force worker failed for video %d: %s",
                            vi, exc,
                        )
                        continue

                    # Update shared state (main thread only — safe)
                    video_diagnostics[vi] = bf_result.get("diagnostics", {})

                    if bf_result.get("matched"):
                        ri = bf_result["ri"]
                        matched_pairs[vi] = (ri, bf_result["result"])
                        matched_recorders.add(ri)
                        total_synced_audio += 1
                        ag = audio_groups[ri]
                        conf = bf_result["result"].get("confidence", 0.0)
                        off = bf_result["result"].get("offset", 0.0)
                        method = bf_result["result"].get("method", "?")
                        bf_pr = bf_result["result"].get("peak_ratio") or 0.0
                        notify("match", {
                            "video": videos[vi]["path"].name,
                            "audio": ag["zoom_dir"].name,
                            "method": f"brute_force:{method}",
                            "offset": off,
                            "confidence": conf,
                            "peak_ratio": bf_pr,
                        })
                    else:
                        diag = bf_result.get("diagnostics", {})
                        notify("info", {
                            "message": (
                                f"No match for {videos[vi]['path'].name} "
                                f"(best: conf={diag.get('confidence', 0):.3f}, "
                                f"peak_ratio={diag.get('peak_ratio', 0):.2f})"
                            ),
                        })

                    notify("progress", {
                        "phase": "brute_force",
                        "bf_index": done_count,
                        "bf_total": n_bf,
                    })

        # ==============================================================
        # Cleanup and build timeline
        # ==============================================================
        self.engine.clear_zoom_cache()

        timeline = self._build_timeline(
            videos, audio_groups, video_infos, audio_wav_by_type,
            matched_pairs, matched_recorders, recorder_durations,
            ts_assignments, video_diagnostics,
        )

        n_matched_ts = sum(
            1 for _vi, (_ri, sd) in matched_pairs.items()
            if sd.get("method") == "timestamp_only"
        )
        n_unique_recorders = len(matched_recorders)

        notify("phase", {
            "name": "done",
            "description": (
                f"Result: {len(matched_pairs)}/{nv} synced "
                f"({total_synced_audio} audio + {n_matched_ts} timestamp) "
                f"| {n_unique_recorders} recorders used"
            ),
            "total_matched": len(matched_pairs),
            "total_videos": nv,
            "audio_matches": total_synced_audio,
            "timestamp_matches": n_matched_ts,
            "recorders_used": n_unique_recorders,
        })

        return timeline
