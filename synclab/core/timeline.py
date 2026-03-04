"""
Timeline construction for the matching pipeline.

Extracted from matcher.py during v1.3.0 refactoring.
Provides functions for building timeline items (synced, video-only,
audio-only) and assembling them into a sorted timeline.

Dependencies: none (pure data transformation).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple


# ---------------------------------------------------------------------------
# Timeline item builders
# ---------------------------------------------------------------------------

def audio_only_item(
    wav_by_type: dict,
    audio_name: str,
    group_id: str,
) -> Dict[str, Any]:
    """Build a timeline item for an unmatched recorder group.

    Parameters
    ----------
    wav_by_type : dict
        Track type -> media info mapping.
    audio_name : str
        Display name for the audio group.
    group_id : str
        Identifier for the source group/card.

    Returns
    -------
    dict
        Timeline item.
    """
    return {
        "type": "audio_only",
        "video_info": None,
        "wav_by_type": wav_by_type,
        "offset": 0.0,
        "confidence": 0.0,
        "method": "n/a",
        "level": 0,
        "video_name": None,
        "audio_name": audio_name,
        "card": group_id,
    }


def video_only_item(
    video_info: dict,
    video_name: str,
    group_id: str,
) -> Dict[str, Any]:
    """Build a timeline item for an unmatched video.

    Parameters
    ----------
    video_info : dict
        Media info dict for the video.
    video_name : str
        Display name for the video file.
    group_id : str
        Identifier for the source group/card.

    Returns
    -------
    dict
        Timeline item.
    """
    return {
        "type": "video_only",
        "video_info": video_info,
        "wav_by_type": {},
        "offset": 0.0,
        "confidence": 0.0,
        "method": "n/a",
        "level": 0,
        "video_name": video_name,
        "audio_name": None,
        "card": group_id,
    }


# ---------------------------------------------------------------------------
# Timeline assembly
# ---------------------------------------------------------------------------

def build_timeline(
    videos: List[dict],
    audio_groups: List[dict],
    video_infos: List[dict],
    audio_wav_by_type: List[dict],
    matched_pairs: Dict[int, Tuple[int, dict]],
    matched_recorders: Set[int],
    recorder_durations: List[float],
    ts_assignments: Dict[int, Tuple[int, float]],
    video_diagnostics: Optional[Dict[int, dict]] = None,
) -> List[Dict[str, Any]]:
    """Build a sorted timeline from matching results.

    Synced items are placed in video order.  Orphan recorder groups
    (those not matched to any video) are inserted chronologically
    relative to the matched items.

    Parameters
    ----------
    videos : list of dict
        Video dicts with ``path`` and ``card`` keys.
    audio_groups : list of dict
        Audio group dicts with ``zoom_dir`` and ``card`` keys.
    video_infos : list of dict
        Media info dicts per video.
    audio_wav_by_type : list of dict
        wav_by_type dicts per audio group.
    matched_pairs : dict
        vi -> (ri, sync_data_dict).
    matched_recorders : set of int
        Set of matched recorder indices.
    recorder_durations : list of float
        Duration per recorder group.
    ts_assignments : dict
        vi -> (ri, predicted_offset).
    video_diagnostics : dict, optional
        vi -> diagnostic record.

    Returns
    -------
    list of dict
        Timeline items.
    """
    if video_diagnostics is None:
        video_diagnostics = {}

    items = []
    orphan_recorders = sorted(
        ri for ri in range(len(audio_groups))
        if ri not in matched_recorders
    )
    orphan_ptr = 0

    for vi in range(len(videos)):
        if vi in matched_pairs:
            ri, sync_data = matched_pairs[vi]
            # Insert orphan recorders that come before this recorder
            while (
                orphan_ptr < len(orphan_recorders)
                and orphan_recorders[orphan_ptr] < ri
            ):
                oi = orphan_recorders[orphan_ptr]
                items.append(audio_only_item(
                    audio_wav_by_type[oi],
                    audio_groups[oi]["zoom_dir"].name,
                    audio_groups[oi].get("card", ""),
                ))
                orphan_ptr += 1

            item = {
                "type": "synced",
                "video_info": video_infos[vi],
                "wav_by_type": audio_wav_by_type[ri],
                "offset": sync_data.get("offset", 0.0),
                "confidence": sync_data.get("confidence", 0.0),
                "peak_ratio": sync_data.get("peak_ratio"),
                "method": sync_data.get("method", "?"),
                "level": sync_data.get("level", 0),
                "video_name": videos[vi]["path"].name,
                "audio_name": audio_groups[ri]["zoom_dir"].name,
                "card": videos[vi].get("card", ""),
                "source_folder": videos[vi].get("source_folder", ""),
            }
            if vi in video_diagnostics:
                item["diagnostics"] = video_diagnostics[vi]
            items.append(item)
        else:
            # Insert orphan recorders before this unmatched video
            while (
                orphan_ptr < len(orphan_recorders)
                and orphan_recorders[orphan_ptr] <= vi
            ):
                oi = orphan_recorders[orphan_ptr]
                items.append(audio_only_item(
                    audio_wav_by_type[oi],
                    audio_groups[oi]["zoom_dir"].name,
                    audio_groups[oi].get("card", ""),
                ))
                orphan_ptr += 1

            item = video_only_item(
                video_infos[vi],
                videos[vi]["path"].name,
                videos[vi].get("card", ""),
            )
            if vi in video_diagnostics:
                item["diagnostics"] = video_diagnostics[vi]
            items.append(item)

    # Append remaining orphan recorders
    while orphan_ptr < len(orphan_recorders):
        oi = orphan_recorders[orphan_ptr]
        items.append(audio_only_item(
            audio_wav_by_type[oi],
            audio_groups[oi]["zoom_dir"].name,
            audio_groups[oi].get("card", ""),
        ))
        orphan_ptr += 1

    return items
