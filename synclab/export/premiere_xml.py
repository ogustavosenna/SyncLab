"""
Premiere Pro XML (FCP XML v5) generator for SyncLab.

Generates Final Cut Pro XML v5 format files compatible with
Adobe Premiere Pro import. Creates a timeline sequence with
video and audio tracks from synced, video-only, and audio-only items.

Timeline items are dicts with:
    - type: "synced", "video_only", or "audio_only"
    - video_info: dict with media info (for synced/video_only)
    - wav_by_type: dict mapping track type -> media info (for synced/audio_only)
    - offset: float, sync offset in seconds (for synced)
    - confidence: float (for synced)
    - method: str (for synced)

Media info dicts contain:
    - path, name, duration, has_audio, video_streams, audio_streams
    - width, height, fps, sample_rate, channels
"""

import uuid as _uuid_mod
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union
from xml.dom import minidom


# Default track type ordering used when no explicit list is provided.
DEFAULT_TRACK_TYPES: List[str] = ["_Tr1", "_Tr2", "_Tr3", "_Tr4", "_LR"]


class PremiereXMLGenerator:
    """Generates FCP XML v5 sequences for import into Adobe Premiere Pro.

    All configuration is provided through the constructor; there is no
    dependency on any global config object.

    Args:
        fps: Timeline frame rate (default 29.97 for NTSC).
        width: Sequence frame width in pixels.
        height: Sequence frame height in pixels.
        sample_rate: Audio sample rate in Hz.
        track_types: Ordered list of audio track type identifiers
            (e.g. ``["_Tr1", "_LR"]``).  Track types present in timeline
            items are laid out in this order; any unrecognised types are
            appended under ``"_Other"``.
    """

    def __init__(
        self,
        fps: float = 29.97,
        width: int = 1920,
        height: int = 1080,
        sample_rate: int = 48000,
        track_types: Optional[List[str]] = None,
    ) -> None:
        self.fps = fps
        self.width = width
        self.height = height
        self.sample_rate = sample_rate
        self.track_types = track_types if track_types is not None else list(DEFAULT_TRACK_TYPES)

        # Internal state reset on each generate() call.
        self._file_registry: Dict[str, str] = {}
        self._file_counter: int = 0

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def fr(self, seconds: float) -> int:
        """Convert a duration in seconds to a frame count at the configured fps."""
        return int(round(seconds * self.fps))

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def generate(
        self,
        timeline_items: Sequence[Dict[str, Any]],
        output_path: Union[str, Path],
        project_name: str = "SyncLab",
    ) -> Path:
        """Build an FCP XML v5 file from *timeline_items* and write it to *output_path*.

        Args:
            timeline_items: Sequence of timeline item dicts (see module docstring).
            output_path: Destination path for the XML file.
            project_name: Name shown as the sequence name inside Premiere.

        Returns:
            The resolved ``Path`` of the written XML file.
        """
        output_path = Path(output_path)

        # Enforce .xml extension for Premiere Pro compatibility.
        if output_path.suffix.lower() != ".xml":
            output_path = output_path.with_suffix(".xml")

        # Determine which audio track types are present and their order.
        track_order = self._resolve_track_order(timeline_items)

        # Reset per-generation state.
        self._file_registry = {}
        self._file_counter = 0

        # --- Root / Project / Sequence --------------------------------------
        # FCP XML v5 requires: <xmeml> → <project> → <children> → <sequence>
        # Premiere Pro expects this hierarchy for correct import.
        xmeml = ET.Element("xmeml")
        xmeml.set("version", "5")

        project = ET.SubElement(xmeml, "project")
        ET.SubElement(project, "name").text = project_name
        children = ET.SubElement(project, "children")

        seq = ET.SubElement(children, "sequence")
        ET.SubElement(seq, "uuid").text = str(_uuid_mod.uuid4())
        ET.SubElement(seq, "name").text = project_name
        ET.SubElement(seq, "duration").text = "0"
        self._write_rate(seq)

        # Timecode
        tc = ET.SubElement(seq, "timecode")
        self._write_rate(tc)
        ET.SubElement(tc, "string").text = "00:00:00:00"
        ET.SubElement(tc, "frame").text = "0"
        ET.SubElement(tc, "displayformat").text = "NDF"

        # --- Media / Video --------------------------------------------------
        media = ET.SubElement(seq, "media")
        video_section = ET.SubElement(media, "video")

        video_format = ET.SubElement(video_section, "format")
        vsc = ET.SubElement(video_format, "samplecharacteristics")
        self._write_rate(vsc)
        ET.SubElement(vsc, "width").text = str(self.width)
        ET.SubElement(vsc, "height").text = str(self.height)
        ET.SubElement(vsc, "anamorphic").text = "FALSE"
        ET.SubElement(vsc, "pixelaspectratio").text = "square"
        ET.SubElement(vsc, "fielddominance").text = "none"

        video_track = ET.SubElement(video_section, "track")

        # --- Media / Audio --------------------------------------------------
        audio_section = ET.SubElement(media, "audio")

        audio_format = ET.SubElement(audio_section, "format")
        asc = ET.SubElement(audio_format, "samplecharacteristics")
        ET.SubElement(asc, "depth").text = "16"
        ET.SubElement(asc, "samplerate").text = str(self.sample_rate)

        # Output channel group (stereo master)
        outputs = ET.SubElement(audio_section, "outputs")
        group = ET.SubElement(outputs, "group")
        ET.SubElement(group, "index").text = "1"
        ET.SubElement(group, "numchannels").text = "2"
        ET.SubElement(group, "downmix").text = "0"
        ch1 = ET.SubElement(group, "channel")
        ET.SubElement(ch1, "index").text = "1"
        ch2 = ET.SubElement(group, "channel")
        ET.SubElement(ch2, "index").text = "2"

        # Camera audio tracks (L / R embedded in video files)
        camera_track_L = ET.SubElement(audio_section, "track")
        camera_track_R = ET.SubElement(audio_section, "track")

        # External audio tracks keyed by track type
        ext_tracks_by_type: Dict[str, Tuple[Any, Optional[Any]]] = {}
        for tt in track_order:
            if tt == "_LR":
                ext_tracks_by_type[tt] = (
                    ET.SubElement(audio_section, "track"),
                    ET.SubElement(audio_section, "track"),
                )
            else:
                ext_tracks_by_type[tt] = (
                    ET.SubElement(audio_section, "track"),
                    None,
                )

        # --- Populate clips --------------------------------------------------
        cursor = 0
        for idx, item in enumerate(timeline_items):
            item_type = item["type"]

            if item_type == "synced":
                cursor = self._place_synced_item(
                    idx, item, cursor,
                    video_track, camera_track_L, camera_track_R,
                    ext_tracks_by_type,
                )

            elif item_type == "video_only":
                cursor = self._place_video_only_item(
                    idx, item, cursor,
                    video_track, camera_track_L, camera_track_R,
                )

            elif item_type == "audio_only":
                cursor = self._place_audio_only_item(
                    idx, item, cursor,
                    ext_tracks_by_type,
                )

        # Update top-level duration to the total cursor position.
        for dur_el in seq.iter("duration"):
            dur_el.text = str(cursor)
            break

        # Assign output channel indices to each audio track.
        output_ch_idx = 1
        for audio_track in audio_section.findall("track"):
            oci_el = ET.SubElement(audio_track, "outputchannelindex")
            oci_el.text = str(output_ch_idx)
            output_ch_idx += 1

        # --- Generate bins by source_folder ----------------------------------
        # NOTE: Bins disabled for now — Premiere Pro requires masterclip
        # structure inside bins which is more complex than simple <clip> refs.
        # TODO: Implement proper masterclip bin format in v1.2.
        # self._generate_bins(children, timeline_items)

        # --- Serialize to file -----------------------------------------------
        self._write_xml(xmeml, output_path)

        return output_path

    # ------------------------------------------------------------------
    # Label color mapping
    # ------------------------------------------------------------------

    @staticmethod
    def _get_label_color(item: Dict[str, Any]) -> str:
        """Return Premiere Pro label color name for a timeline item.

        Uses the same badge logic as server.py _compute_confidence_badge:
          high   (conf >= 0.30 AND peak_ratio >= 2.0) → Forest (green)
          medium (conf >= 0.10 OR ...)                 → Mango  (orange)
          low    (timestamp_only, weak match)          → Iris   (blue)
          video_only                                   → Rose   (red/pink)
          audio_only                                   → Lavender
        """
        item_type = item.get("type", "")
        if item_type == "video_only":
            return "Rose"
        if item_type == "audio_only":
            return "Lavender"

        method = item.get("method", "")
        if method in ("timestamp_only", "timestamp_fallback", "n/a", "vad_skip"):
            return "Iris"

        conf = item.get("confidence", 0.0)
        peak_ratio = item.get("peak_ratio") or 0.0

        if conf >= 0.30 and peak_ratio >= 2.0:
            return "Forest"
        if conf >= 0.10 or (conf >= 0.05 and peak_ratio >= 1.5):
            return "Mango"
        return "Iris"

    # ------------------------------------------------------------------
    # Item placement helpers
    # ------------------------------------------------------------------

    def _place_synced_item(
        self,
        idx: int,
        item: Dict[str, Any],
        cursor: int,
        video_track: ET.Element,
        camera_track_L: ET.Element,
        camera_track_R: ET.Element,
        ext_tracks: Dict[str, Tuple[Any, Optional[Any]]],
    ) -> int:
        """Place a synced (video + external audio) item on the timeline.

        Returns the new cursor position (in frames).
        """
        video_info = item["video_info"]
        offset = item.get("offset", 0.0)
        video_dur = max(self.fr(video_info["duration"]), 1)

        label_color = self._get_label_color(item)

        vid_clip_id = f"clipitem-v{idx}"
        ci_v = self._create_video_clip(
            video_track, video_info, cursor, cursor + video_dur, vid_clip_id,
            label=label_color,
        )

        link_ids = [vid_clip_id]

        # Embed camera audio if the video has audio streams.
        if video_info.get("has_audio"):
            cam_L_id = f"clipitem-cam{idx}-L"
            cam_R_id = f"clipitem-cam{idx}-R"
            ci_cam_L = self._create_audio_clip(
                camera_track_L, video_info, cursor, cursor + video_dur,
                cam_L_id, in_pt=0, trackindex=1,
            )
            ci_cam_R = self._create_audio_clip(
                camera_track_R, video_info, cursor, cursor + video_dur,
                cam_R_id, in_pt=0, trackindex=2,
            )
            link_ids.extend([cam_L_id, cam_R_id])
            self._write_links(ci_v, link_ids)
            self._write_links(ci_cam_L, link_ids)
            self._write_links(ci_cam_R, link_ids)

        # Skip external audio for timestamp_only matches — the offset is
        # unconfirmed by audio correlation and would place wrong audio.
        method = item.get("method", "")
        if method in ("timestamp_only", "timestamp_fallback"):
            return cursor + video_dur

        # Place external audio tracks aligned by sync offset.
        offset_frames = self.fr(abs(offset))
        for tt, wav_info in item.get("wav_by_type", {}).items():
            if tt not in ext_tracks:
                continue
            audio_in = offset_frames if offset >= 0 else 0
            audio_dur = max(self.fr(wav_info["duration"]), 1)
            audio_end = cursor + min(video_dur, max(1, audio_dur - audio_in))
            tracks = ext_tracks[tt]
            num_channels = self._detect_channels(wav_info, is_video=False)

            if num_channels >= 2 and tracks[1] is not None:
                ext_L_id = f"clipitem-ext{idx}_{tt}-L"
                ext_R_id = f"clipitem-ext{idx}_{tt}-R"
                ci_ext_L = self._create_audio_clip(
                    tracks[0], wav_info, cursor, audio_end,
                    ext_L_id, in_pt=audio_in, trackindex=1,
                )
                ci_ext_R = self._create_audio_clip(
                    tracks[1], wav_info, cursor, audio_end,
                    ext_R_id, in_pt=audio_in, trackindex=2,
                )
                self._write_links(ci_ext_L, [ext_L_id, ext_R_id])
                self._write_links(ci_ext_R, [ext_L_id, ext_R_id])
            else:
                self._create_audio_clip(
                    tracks[0], wav_info, cursor, audio_end,
                    f"clipitem-ext{idx}_{tt}", in_pt=audio_in,
                )

        return cursor + video_dur

    def _place_video_only_item(
        self,
        idx: int,
        item: Dict[str, Any],
        cursor: int,
        video_track: ET.Element,
        camera_track_L: ET.Element,
        camera_track_R: ET.Element,
    ) -> int:
        """Place a video-only item (no external audio) on the timeline.

        Returns the new cursor position (in frames).
        """
        video_info = item["video_info"]
        video_dur = max(self.fr(video_info["duration"]), 1)
        label_color = self._get_label_color(item)

        vid_clip_id = f"clipitem-v{idx}"
        ci_v = self._create_video_clip(
            video_track, video_info, cursor, cursor + video_dur, vid_clip_id,
            label=label_color,
        )

        link_ids = [vid_clip_id]

        if video_info.get("has_audio"):
            cam_L_id = f"clipitem-cam{idx}-L"
            cam_R_id = f"clipitem-cam{idx}-R"
            ci_cam_L = self._create_audio_clip(
                camera_track_L, video_info, cursor, cursor + video_dur,
                cam_L_id, in_pt=0, trackindex=1,
            )
            ci_cam_R = self._create_audio_clip(
                camera_track_R, video_info, cursor, cursor + video_dur,
                cam_R_id, in_pt=0, trackindex=2,
            )
            link_ids.extend([cam_L_id, cam_R_id])
            self._write_links(ci_v, link_ids)
            self._write_links(ci_cam_L, link_ids)
            self._write_links(ci_cam_R, link_ids)

        return cursor + video_dur

    def _place_audio_only_item(
        self,
        idx: int,
        item: Dict[str, Any],
        cursor: int,
        ext_tracks: Dict[str, Tuple[Any, Optional[Any]]],
    ) -> int:
        """Place an audio-only item (no video) on the timeline.

        Returns the new cursor position (in frames).
        """
        wav_by_type = item.get("wav_by_type", {})
        if not wav_by_type:
            return cursor

        # Duration of the longest WAV determines the block size.
        block_dur = max(
            self.fr(max((w["duration"] for w in wav_by_type.values()), default=5)),
            1,
        )

        for tt, wav_info in wav_by_type.items():
            if tt not in ext_tracks:
                continue
            wav_dur = max(self.fr(wav_info["duration"]), 1)
            tracks = ext_tracks[tt]
            num_channels = self._detect_channels(wav_info, is_video=False)

            if num_channels >= 2 and tracks[1] is not None:
                ext_L_id = f"clipitem-ext{idx}_{tt}-L"
                ext_R_id = f"clipitem-ext{idx}_{tt}-R"
                ci_ext_L = self._create_audio_clip(
                    tracks[0], wav_info, cursor, cursor + wav_dur,
                    ext_L_id, trackindex=1,
                )
                ci_ext_R = self._create_audio_clip(
                    tracks[1], wav_info, cursor, cursor + wav_dur,
                    ext_R_id, trackindex=2,
                )
                self._write_links(ci_ext_L, [ext_L_id, ext_R_id])
                self._write_links(ci_ext_R, [ext_L_id, ext_R_id])
            else:
                self._create_audio_clip(
                    tracks[0], wav_info, cursor, cursor + wav_dur,
                    f"clipitem-ext{idx}_{tt}",
                )

        return cursor + block_dur

    # ------------------------------------------------------------------
    # Bin generation (source folder structure)
    # ------------------------------------------------------------------

    def _generate_bins(
        self,
        children: ET.Element,
        timeline_items: Sequence[Dict[str, Any]],
    ) -> None:
        """Generate FCP XML v5 bins organized by source folder.

        Creates a bin hierarchy in the project ``<children>`` element:
        - One bin per ``source_folder`` (e.g. "Dia 01")
        - Each folder bin contains two sub-bins: "Videos" and "Audio"
        - Video clips go into the Videos sub-bin
        - Audio clips go into the Audio sub-bin

        Items without a ``source_folder`` are grouped under "Unsorted".
        """
        # Group items by source_folder
        folder_groups: Dict[str, List[Dict[str, Any]]] = {}
        for item in timeline_items:
            sf = item.get("source_folder", "") or "Unsorted"
            if sf not in folder_groups:
                folder_groups[sf] = []
            folder_groups[sf].append(item)

        if not folder_groups:
            return

        for folder_name in sorted(folder_groups.keys()):
            items = folder_groups[folder_name]

            folder_bin = ET.SubElement(children, "bin")
            ET.SubElement(folder_bin, "name").text = folder_name
            bin_children = ET.SubElement(folder_bin, "children")

            # Videos sub-bin
            video_items = [
                it for it in items
                if it["type"] in ("synced", "video_only") and it.get("video_info")
            ]
            if video_items:
                video_bin = ET.SubElement(bin_children, "bin")
                ET.SubElement(video_bin, "name").text = "Videos"
                vb_children = ET.SubElement(video_bin, "children")
                for it in video_items:
                    self._create_bin_clip(vb_children, it["video_info"], is_video=True)

            # Audio sub-bin
            audio_infos = []
            for it in items:
                for _tt, wav_info in it.get("wav_by_type", {}).items():
                    audio_infos.append(wav_info)
            if audio_infos:
                audio_bin = ET.SubElement(bin_children, "bin")
                ET.SubElement(audio_bin, "name").text = "Audio"
                ab_children = ET.SubElement(audio_bin, "children")
                seen_paths = set()
                for wav_info in audio_infos:
                    fpath = str(wav_info.get("path", ""))
                    if fpath in seen_paths:
                        continue
                    seen_paths.add(fpath)
                    self._create_bin_clip(ab_children, wav_info, is_video=False)

    def _create_bin_clip(
        self,
        parent: ET.Element,
        media_info: Dict[str, Any],
        is_video: bool = False,
    ) -> ET.Element:
        """Create a ``<clip>`` element inside a bin referencing a media file.

        Re-uses the same ``_file_registry`` for de-duplication so that
        bin clips reference the same ``<file>`` elements as timeline clips.
        """
        clip = ET.SubElement(parent, "clip")
        clip.set("id", f"bin-clip-{_uuid_mod.uuid4().hex[:8]}")
        ET.SubElement(clip, "name").text = media_info.get("name", "unknown")
        dur_frames = max(self.fr(media_info.get("duration", 0)), 1)
        ET.SubElement(clip, "duration").text = str(dur_frames)
        self._write_rate(clip)

        self._create_file_element(clip, media_info, is_video=is_video)

        return clip

    # ------------------------------------------------------------------
    # Track order resolution
    # ------------------------------------------------------------------

    def _resolve_track_order(self, timeline_items: Sequence[Dict[str, Any]]) -> List[str]:
        """Determine the ordered list of audio track types present in *timeline_items*."""
        existing_types: set = set()
        for item in timeline_items:
            for tt in item.get("wav_by_type", {}).keys():
                existing_types.add(tt)

        track_order = [tt for tt in self.track_types if tt in existing_types]
        if "_Other" in existing_types:
            track_order.append("_Other")
        if not track_order:
            track_order = ["_Tr1"]
        return track_order

    # ------------------------------------------------------------------
    # XML element builders
    # ------------------------------------------------------------------

    def _write_rate(self, parent: ET.Element) -> ET.Element:
        """Append a ``<rate>`` element with timebase/ntsc values for the configured fps."""
        rate_el = ET.SubElement(parent, "rate")
        if abs(self.fps - 29.97) < 0.01:
            ET.SubElement(rate_el, "timebase").text = "30"
            ET.SubElement(rate_el, "ntsc").text = "TRUE"
        elif abs(self.fps - 23.976) < 0.01:
            ET.SubElement(rate_el, "timebase").text = "24"
            ET.SubElement(rate_el, "ntsc").text = "TRUE"
        else:
            ET.SubElement(rate_el, "timebase").text = str(int(round(self.fps)))
            ET.SubElement(rate_el, "ntsc").text = "FALSE"
        return rate_el

    def _format_file_url(self, filepath: Union[str, Path]) -> str:
        """Convert a filesystem path to a ``file://localhost/`` URL for FCP XML v5.

        Uses the ``file://localhost/`` scheme consistently on all platforms,
        which is the format specified by the FCP XML standard and most
        compatible with Premiere Pro import.
        """
        s = str(filepath).replace("\\", "/")

        # Windows absolute path (e.g. "C:/Users/...")
        if len(s) >= 2 and s[1] == ":":
            parts = s.split("/")
            encoded = "/".join(urllib.parse.quote(part, safe=":") for part in parts)
            return f"file://localhost/{encoded}"

        # Unix / fallback
        if not s.startswith("/"):
            s = "/" + s
        parts = s.split("/")
        encoded = "/".join(urllib.parse.quote(part, safe="/") for part in parts)
        return f"file://localhost{encoded}"

    def _detect_channels(self, media_info: Dict[str, Any], is_video: bool = False) -> int:
        """Determine the number of audio channels for a media file.

        Priority: explicit ``channels`` metadata → ``audio_streams`` count →
        filename heuristics → sensible defaults.
        """
        # 1. Explicit channel count from ffprobe metadata
        num_ch = media_info.get("channels", 0)
        if num_ch > 0:
            return num_ch

        # 2. Audio stream count (each stream is typically mono or stereo)
        audio_streams = media_info.get("audio_streams", 0)
        if audio_streams > 0:
            return max(audio_streams, 2) if is_video else audio_streams

        # 3. Video files default to stereo (most cameras record stereo)
        if is_video:
            return 2

        # 4. Filename heuristics for ZOOM recorder track types
        name = media_info.get("name", "").upper()
        if "_LR" in name:
            return 2
        # _Tr1, _Tr2, etc. are individual mono tracks
        if "_TR" in name:
            return 1

        # 5. Default: assume mono for external audio, stereo for unknown
        return 1

    def _create_file_element(
        self,
        parent: ET.Element,
        media_info: Dict[str, Any],
        is_video: bool = False,
    ) -> ET.Element:
        """Create (or reference) a ``<file>`` element for *media_info*.

        Premiere requires each physical file to appear with a unique ``id``
        the first time and then be referenced by that ``id`` subsequently.
        """
        file_path = str(media_info.get("path", ""))

        # If already registered, emit a short reference.
        if file_path in self._file_registry:
            file_el = ET.SubElement(parent, "file")
            file_el.set("id", self._file_registry[file_path])
            return file_el

        # First occurrence -- full definition.
        self._file_counter += 1
        file_id = f"file-{self._file_counter}"
        self._file_registry[file_path] = file_id

        file_el = ET.SubElement(parent, "file")
        file_el.set("id", file_id)
        ET.SubElement(file_el, "name").text = media_info.get("name", "unknown")
        ET.SubElement(file_el, "pathurl").text = self._format_file_url(file_path)
        self._write_rate(file_el)

        dur_frames = max(self.fr(media_info.get("duration", 0)), 1)
        ET.SubElement(file_el, "duration").text = str(dur_frames)

        fm = ET.SubElement(file_el, "media")

        # Video stream description
        if is_video and media_info.get("video_streams", 0) > 0:
            vid = ET.SubElement(fm, "video")
            sc = ET.SubElement(vid, "samplecharacteristics")
            self._write_rate(sc)
            ET.SubElement(sc, "width").text = str(media_info.get("width", self.width))
            ET.SubElement(sc, "height").text = str(media_info.get("height", self.height))

        # Audio stream description
        if media_info.get("has_audio") or media_info.get("audio_streams", 0) > 0:
            aud = ET.SubElement(fm, "audio")
            sc = ET.SubElement(aud, "samplecharacteristics")
            ET.SubElement(sc, "depth").text = "16"
            ET.SubElement(sc, "samplerate").text = str(
                media_info.get("sample_rate", self.sample_rate)
            )
            num_ch = self._detect_channels(media_info, is_video)
            ET.SubElement(aud, "channelcount").text = str(num_ch)

        return file_el

    def _create_video_clip(
        self,
        track: ET.Element,
        media_info: Dict[str, Any],
        start: int,
        end: int,
        clip_id: str,
        label: str = "",
    ) -> ET.Element:
        """Append a video ``<clipitem>`` to *track*."""
        ci = ET.SubElement(track, "clipitem")
        ci.set("id", clip_id)
        ET.SubElement(ci, "name").text = media_info.get("name", "clip")
        ET.SubElement(ci, "enabled").text = "TRUE"
        ET.SubElement(ci, "start").text = str(start)
        ET.SubElement(ci, "end").text = str(end)
        ET.SubElement(ci, "in").text = "0"
        ET.SubElement(ci, "out").text = str(end - start)
        self._write_rate(ci)
        self._create_file_element(ci, media_info, is_video=True)
        if label:
            labels_el = ET.SubElement(ci, "labels")
            ET.SubElement(labels_el, "label2").text = label
        return ci

    def _create_audio_clip(
        self,
        track: ET.Element,
        media_info: Dict[str, Any],
        start: int,
        end: int,
        clip_id: str,
        in_pt: int = 0,
        trackindex: int = 1,
    ) -> ET.Element:
        """Append an audio ``<clipitem>`` to *track*."""
        ci = ET.SubElement(track, "clipitem")
        ci.set("id", clip_id)
        ET.SubElement(ci, "name").text = media_info.get("name", "clip")
        ET.SubElement(ci, "enabled").text = "TRUE"
        ET.SubElement(ci, "start").text = str(start)
        ET.SubElement(ci, "end").text = str(end)
        ET.SubElement(ci, "in").text = str(in_pt)
        ET.SubElement(ci, "out").text = str(in_pt + (end - start))
        self._write_rate(ci)

        has_video = media_info.get("video_streams", 0) > 0
        self._create_file_element(ci, media_info, is_video=has_video)

        source_track = ET.SubElement(ci, "sourcetrack")
        ET.SubElement(source_track, "mediatype").text = "audio"
        ET.SubElement(source_track, "trackindex").text = str(trackindex)
        return ci

    def _write_links(self, clipitem: ET.Element, link_ids: Sequence[str]) -> None:
        """Append ``<link>`` elements to *clipitem* referencing all *link_ids*."""
        for ref_id in link_ids:
            link = ET.SubElement(clipitem, "link")
            ET.SubElement(link, "linkclipref").text = ref_id

    # ------------------------------------------------------------------
    # XML serialization
    # ------------------------------------------------------------------

    def _write_xml(self, root: ET.Element, output_path: Path) -> None:
        """Serialize the ElementTree *root* to a pretty-printed FCP XML v5 file."""
        raw = ET.tostring(root, encoding="unicode")
        try:
            dom = minidom.parseString(raw)
            pretty = dom.toprettyxml(indent="  ")
            lines = pretty.split("\n")
            # Ensure a clean XML declaration.
            if lines[0].startswith("<?xml"):
                lines[0] = '<?xml version="1.0" encoding="UTF-8"?>'
            final_lines = [lines[0], "<!DOCTYPE xmeml>"]
            final_lines.extend(line for line in lines[1:] if line.strip())
            final = "\n".join(final_lines)
        except Exception:
            final = '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n' + raw

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as fh:
            fh.write(final)
