"""
Tests for synclab/scanner/scanner.py
Priority 8: File discovery logic with temporary directories.
"""

import pytest
from pathlib import Path

from synclab.scanner.scanner import scan_folders


# ---------------------------------------------------------------------------
# Video Discovery
# ---------------------------------------------------------------------------

class TestVideoDiscovery:
    """Test video file discovery."""

    def test_finds_video_files(self, tmp_path):
        """Should discover .mov, .mp4, .mxf, .avi files."""
        (tmp_path / "clip1.mov").touch()
        (tmp_path / "clip2.mp4").touch()
        (tmp_path / "clip3.mxf").touch()
        (tmp_path / "clip4.avi").touch()
        (tmp_path / "not_video.txt").touch()

        videos, _ = scan_folders([str(tmp_path)], [])
        assert len(videos) == 4, f"Expected 4 videos, found {len(videos)}"

    def test_ignores_non_video_files(self, tmp_path):
        """Should not include .txt, .wav, .jpg files."""
        (tmp_path / "audio.wav").touch()
        (tmp_path / "image.jpg").touch()
        (tmp_path / "document.txt").touch()

        videos, _ = scan_folders([str(tmp_path)], [])
        assert len(videos) == 0

    def test_recursive_discovery(self, tmp_path):
        """Should find videos in subdirectories."""
        sub = tmp_path / "day1" / "camera"
        sub.mkdir(parents=True)
        (sub / "clip1.mov").touch()

        videos, _ = scan_folders([str(tmp_path)], [])
        assert len(videos) == 1

    def test_case_insensitive_extensions(self, tmp_path):
        """Should match extensions regardless of case (.MOV, .Mov, .mov)."""
        (tmp_path / "clip1.MOV").touch()
        (tmp_path / "clip2.Mp4").touch()

        videos, _ = scan_folders([str(tmp_path)], [])
        assert len(videos) == 2

    def test_empty_folder(self, tmp_path):
        """Empty folder should return empty list."""
        videos, _ = scan_folders([str(tmp_path)], [])
        assert len(videos) == 0

    def test_multiple_folders(self, tmp_path):
        """Should scan multiple video folders."""
        folder1 = tmp_path / "day1"
        folder2 = tmp_path / "day2"
        folder1.mkdir()
        folder2.mkdir()
        (folder1 / "clip1.mov").touch()
        (folder2 / "clip2.mov").touch()

        videos, _ = scan_folders([str(folder1), str(folder2)], [])
        assert len(videos) == 2


# ---------------------------------------------------------------------------
# Audio Group Discovery
# ---------------------------------------------------------------------------

class TestAudioGroupDiscovery:
    """Test ZOOM recorder group discovery."""

    def test_finds_zoom_groups(self, tmp_path):
        """Should discover ZOOM recorder groups (directories with WAV files matching track types)."""
        zoom1 = tmp_path / "ZOOM0001"
        zoom1.mkdir()
        (zoom1 / "ZOOM0001_Tr1.WAV").touch()
        (zoom1 / "ZOOM0001_LR.WAV").touch()

        zoom2 = tmp_path / "ZOOM0002"
        zoom2.mkdir()
        (zoom2 / "ZOOM0002_Tr1.WAV").touch()

        _, audio_groups = scan_folders([], [str(tmp_path)])
        assert len(audio_groups) >= 2, f"Expected at least 2 audio groups, found {len(audio_groups)}"

    def test_audio_groups_have_wav_files(self, tmp_path):
        """Each audio group should have wav_files list."""
        zoom = tmp_path / "ZOOM0001"
        zoom.mkdir()
        (zoom / "ZOOM0001_Tr1.WAV").touch()
        (zoom / "ZOOM0001_LR.WAV").touch()

        _, audio_groups = scan_folders([], [str(tmp_path)])
        if audio_groups:
            group = audio_groups[0]
            assert "wav_files" in group
            assert "zoom_dir" in group
            assert len(group["wav_files"]) >= 1

    def test_nested_zoom_folders(self, tmp_path):
        """Should find ZOOM groups in nested directory structures."""
        nested = tmp_path / "AudioFiles" / "Day1" / "ZOOM0001"
        nested.mkdir(parents=True)
        (nested / "ZOOM0001_Tr1.WAV").touch()

        _, audio_groups = scan_folders([], [str(tmp_path)])
        assert len(audio_groups) >= 1


# ---------------------------------------------------------------------------
# Combined Scan
# ---------------------------------------------------------------------------

class TestCombinedScan:
    """Test scanning both video and audio folders together."""

    def test_independent_scanning(self, tmp_path):
        """Video and audio discovery should be independent."""
        vid_folder = tmp_path / "videos"
        aud_folder = tmp_path / "audio"
        vid_folder.mkdir()
        aud_folder.mkdir()

        (vid_folder / "clip1.mov").touch()
        zoom = aud_folder / "ZOOM0001"
        zoom.mkdir()
        (zoom / "ZOOM0001_Tr1.WAV").touch()

        videos, audio_groups = scan_folders([str(vid_folder)], [str(aud_folder)])
        assert len(videos) == 1
        assert len(audio_groups) >= 1

    def test_custom_extensions(self, tmp_path):
        """Should respect custom video extensions."""
        (tmp_path / "clip1.r3d").touch()  # RED camera
        (tmp_path / "clip2.mov").touch()

        # Only look for .r3d
        videos, _ = scan_folders(
            [str(tmp_path)], [],
            video_extensions=[".r3d"]
        )
        assert len(videos) == 1
        assert videos[0]["name"].endswith(".r3d")
