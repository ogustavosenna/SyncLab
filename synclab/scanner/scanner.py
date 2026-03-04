"""
SyncLab Scanner - Generic File Discovery
Discovers video files and audio groups (ZOOM recorders) from user-specified folders.
"""

from pathlib import Path
from typing import List, Dict, Tuple


def scan_folders(
    video_folders: List[str],
    audio_folders: List[str],
    video_extensions: List[str] = None,
    audio_extensions: List[str] = None,
    track_types: List[str] = None,
) -> Tuple[List[Dict], List[Dict]]:
    """Scan folders to discover videos and audio groups.

    Args:
        video_folders: List of folder paths containing video files.
        audio_folders: List of folder paths containing audio files (ZOOM folders).
        video_extensions: Accepted video extensions (default: .mov, .mp4, .mxf, .avi).
        audio_extensions: Accepted audio extensions (default: .wav).
        track_types: Track type suffixes to detect ZOOM groups (default: _Tr1, _LR, etc.).

    Returns:
        Tuple of (videos, audio_groups):
        - videos: List of dicts with 'path' (Path), 'name' (str)
        - audio_groups: List of dicts with 'zoom_dir' (Path), 'wav_files' (List[Path])
    """
    if video_extensions is None:
        video_extensions = [".mov", ".mp4", ".mxf", ".avi"]
    if audio_extensions is None:
        audio_extensions = [".wav"]
    if track_types is None:
        track_types = ["_Tr1", "_Tr2", "_Tr3", "_Tr4", "_LR"]

    # Normalize extensions to lowercase
    video_extensions = [ext.lower() for ext in video_extensions]
    audio_extensions = [ext.lower() for ext in audio_extensions]

    videos = _discover_videos(video_folders, video_extensions)
    audio_groups = _discover_audio_groups(audio_folders, audio_extensions, track_types)

    return videos, audio_groups


def _discover_videos(folders: List[str], extensions: List[str]) -> List[Dict]:
    """Find all video files in the given folders (recursive)."""
    videos = []
    seen_paths = set()

    for folder in folders:
        folder_path = Path(folder)
        if not folder_path.is_dir():
            continue

        for ext in extensions:
            for file_path in sorted(folder_path.rglob(f"*{ext}")):
                # Skip hidden files and temp files
                if file_path.name.startswith(".") or file_path.name.startswith("_"):
                    continue

                canonical = str(file_path.resolve())
                if canonical in seen_paths:
                    continue
                seen_paths.add(canonical)

                # Compute source_folder: first subfolder relative to scan root
                try:
                    rel = file_path.relative_to(folder_path)
                    source_folder = rel.parts[0] if len(rel.parts) > 1 else folder_path.name
                except ValueError:
                    source_folder = folder_path.name

                videos.append({
                    "path": file_path,
                    "name": file_path.name,
                    "source_folder": source_folder,
                })

    # Sort by name for consistent ordering
    videos.sort(key=lambda v: v["name"])
    return videos


def _discover_audio_groups(
    folders: List[str],
    extensions: List[str],
    track_types: List[str],
) -> List[Dict]:
    """Find all audio groups (ZOOM recorder folders) in the given folders.

    Audio groups are detected by finding folders containing WAV files
    with track type suffixes (e.g., ZOOM0001_Tr1.WAV, ZOOM0001_LR.WAV).
    Also supports flat folders with ZOOM-named WAV files.
    """
    audio_groups = []
    seen_dirs = set()

    for folder in folders:
        folder_path = Path(folder)
        if not folder_path.is_dir():
            continue

        # Strategy 1: Look for ZOOM-style subdirectories
        # (folders named ZOOM0001, ZOOM0002, etc. containing WAV files)
        _find_zoom_subdirs(folder_path, extensions, track_types, audio_groups, seen_dirs, folder_path)

        # Strategy 2: Look for ZOOM-named WAV files in the folder itself
        _find_zoom_wavs_in_folder(folder_path, extensions, track_types, audio_groups, seen_dirs, folder_path)

        # Strategy 3: Recurse into subdirectories
        for subdir in sorted(folder_path.iterdir()):
            if subdir.is_dir() and not subdir.name.startswith("."):
                _find_zoom_subdirs(subdir, extensions, track_types, audio_groups, seen_dirs, folder_path)
                _find_zoom_wavs_in_folder(subdir, extensions, track_types, audio_groups, seen_dirs, folder_path)

                # One more level deep (for structures like Audio/Card01/ZOOM0001/)
                for subsubdir in sorted(subdir.iterdir()):
                    if subsubdir.is_dir() and not subsubdir.name.startswith("."):
                        _find_zoom_subdirs(subsubdir, extensions, track_types, audio_groups, seen_dirs, folder_path)

    # Sort by source folder first, then by directory name.
    # This keeps ZOOM groups from the same source together and prevents
    # cross-day interleaving when multiple audio folders are loaded
    # (e.g., ZOOM0001 from day2 stays separate from ZOOM0001 from day3).
    audio_groups.sort(key=lambda g: (g.get("source_folder", ""), g["zoom_dir"].name))
    return audio_groups


def _find_zoom_subdirs(
    parent: Path,
    extensions: List[str],
    track_types: List[str],
    audio_groups: List[Dict],
    seen_dirs: set,
    scan_root: Path = None,
):
    """Find ZOOM-style subdirectories (e.g., ZOOM0001/) containing WAV files."""
    for subdir in sorted(parent.iterdir()):
        if not subdir.is_dir():
            continue
        if subdir.name.startswith(".") or subdir.name.startswith("_"):
            continue

        canonical = str(subdir.resolve())
        if canonical in seen_dirs:
            continue

        # Check if this directory contains audio files with track types
        wav_files = _get_wav_files(subdir, extensions)
        if not wav_files:
            continue

        # Check if any WAV has a track type suffix (ZOOM-style naming)
        has_track = any(
            any(tt in f.stem for tt in track_types)
            for f in wav_files
        )

        if has_track:
            seen_dirs.add(canonical)
            # Compute source_folder from scan root
            source_folder = _compute_source_folder(subdir, scan_root)
            audio_groups.append({
                "zoom_dir": subdir,
                "wav_files": wav_files,
                "source_folder": source_folder,
            })


def _find_zoom_wavs_in_folder(
    folder: Path,
    extensions: List[str],
    track_types: List[str],
    audio_groups: List[Dict],
    seen_dirs: set,
    scan_root: Path = None,
):
    """Find ZOOM-named WAV files directly in a folder (not in subdirs).

    Groups files by their base name (e.g., ZOOM0001_Tr1.WAV and ZOOM0001_LR.WAV
    both belong to the ZOOM0001 group).
    """
    # Skip if this folder was already added as a ZOOM directory
    if str(folder.resolve()) in seen_dirs:
        return

    wav_files = _get_wav_files(folder, extensions, recursive=False)
    if not wav_files:
        return

    # Group by base name (strip track type suffix)
    groups = {}
    for wav in wav_files:
        base = wav.stem
        for tt in track_types:
            if tt in base:
                base = base.replace(tt, "")
                break
        if base not in groups:
            groups[base] = []
        groups[base].append(wav)

    # Only add groups that have track-typed files
    for base, files in sorted(groups.items()):
        has_track = any(
            any(tt in f.stem for tt in track_types)
            for f in files
        )
        if not has_track:
            continue

        canonical = f"{folder.resolve()}:{base}"
        if canonical in seen_dirs:
            continue
        seen_dirs.add(canonical)

        # Create a virtual "zoom_dir" using the folder + base name
        source_folder = _compute_source_folder(folder, scan_root)
        audio_groups.append({
            "zoom_dir": folder / base,  # Virtual path for display
            "wav_files": sorted(files),
            "source_folder": source_folder,
        })


def _compute_source_folder(path: Path, scan_root: Path = None) -> str:
    """Compute the source folder name relative to the scan root.

    Returns the first subfolder name relative to scan_root,
    or the scan_root name itself if the path is directly inside it.
    """
    if scan_root is None:
        return path.name
    try:
        rel = path.relative_to(scan_root)
        return rel.parts[0] if len(rel.parts) > 1 else scan_root.name
    except ValueError:
        return path.name


def _get_wav_files(folder: Path, extensions: List[str], recursive=True) -> List[Path]:
    """Get all audio files in a folder."""
    files = []
    if recursive:
        for ext in extensions:
            files.extend(folder.rglob(f"*{ext}"))
    else:
        for ext in extensions:
            files.extend(folder.glob(f"*{ext}"))

    # Filter out hidden/temp files
    files = [
        f for f in files
        if not f.name.startswith(".") and not f.name.startswith("_")
    ]
    return sorted(files)
