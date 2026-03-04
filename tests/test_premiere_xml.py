"""
Tests for synclab/export/premiere_xml.py
Priority 6: Validates XML export structure and label colors.
"""

import xml.etree.ElementTree as ET
import pytest
from pathlib import Path

from synclab.export.premiere_xml import PremiereXMLGenerator


@pytest.fixture
def generator():
    """Create a PremiereXMLGenerator with default settings."""
    return PremiereXMLGenerator(
        fps=29.97,
        width=1920,
        height=1080,
        sample_rate=48000,
        track_types=["_Tr1", "_LR"],
    )


@pytest.fixture
def synced_item(mock_video_info, mock_wav_by_type):
    """Create a synced timeline item."""
    return {
        "type": "synced",
        "video_info": mock_video_info(duration=60.0, name="P1099001.MOV"),
        "wav_by_type": mock_wav_by_type(duration=600.0),
        "offset": 120.5,
        "confidence": 0.45,
        "peak_ratio": 8.0,
        "method": "xcorr_envelope",
        "level": 3,
        "video_name": "P1099001.MOV",
        "audio_name": "ZOOM0001",
        "card": "A",
    }


@pytest.fixture
def video_only_item(mock_video_info):
    """Create a video-only (unmatched) timeline item."""
    return {
        "type": "video_only",
        "video_info": mock_video_info(duration=30.0, name="P1099050.MOV"),
        "wav_by_type": {},
        "offset": 0,
        "confidence": 0.0,
        "peak_ratio": 0.0,
        "method": "n/a",
        "level": 0,
        "video_name": "P1099050.MOV",
        "audio_name": None,
        "card": "B",
    }


@pytest.fixture
def timestamp_only_item(mock_video_info, mock_wav_by_type):
    """Create a timestamp_only (low confidence) timeline item."""
    return {
        "type": "synced",
        "video_info": mock_video_info(duration=45.0, name="P1099099.MOV"),
        "wav_by_type": mock_wav_by_type(duration=600.0),
        "offset": 200.0,
        "confidence": 0.03,
        "peak_ratio": 1.1,
        "method": "timestamp_only",
        "level": 0,
        "video_name": "P1099099.MOV",
        "audio_name": "ZOOM0008",
        "card": "C",
    }


# ---------------------------------------------------------------------------
# XML Generation
# ---------------------------------------------------------------------------

class TestPremiereXMLGeneration:
    """Test XML generation from timeline items."""

    def test_generates_valid_xml(self, generator, synced_item, tmp_path):
        """Generated XML should be parseable."""
        timeline = [synced_item]
        output = tmp_path / "test_sync.xml"
        generator.generate(timeline, str(output))

        # Should be parseable
        tree = ET.parse(str(output))
        root = tree.getroot()
        assert root.tag == "xmeml"

    def test_correct_number_of_video_clips(self, generator, synced_item, video_only_item, tmp_path):
        """Number of video clipitems should match timeline items."""
        timeline = [synced_item, video_only_item]
        output = tmp_path / "test_count.xml"
        generator.generate(timeline, str(output))

        tree = ET.parse(str(output))
        root = tree.getroot()

        # Find all clipitems in video tracks
        video_clips = root.findall(".//video/track/clipitem")
        assert len(video_clips) == 2, f"Expected 2 video clips, found {len(video_clips)}"

    def test_has_sequence_element(self, generator, synced_item, tmp_path):
        """XML must contain a sequence element with name."""
        timeline = [synced_item]
        output = tmp_path / "test_seq.xml"
        generator.generate(timeline, str(output))

        tree = ET.parse(str(output))
        root = tree.getroot()
        seq = root.find(".//sequence")
        assert seq is not None, "No <sequence> element found"
        name = seq.find("name")
        assert name is not None, "Sequence must have a <name>"


# ---------------------------------------------------------------------------
# Label Colors
# ---------------------------------------------------------------------------

class TestLabelColors:
    """Test that label colors are correctly assigned in XML."""

    def test_label_color_static_method(self, generator):
        """Test the _get_label_color static method directly."""
        # High confidence synced -> Forest
        item = {"type": "synced", "confidence": 0.45, "peak_ratio": 8.0, "method": "xcorr"}
        assert PremiereXMLGenerator._get_label_color(item) == "Forest"

        # Medium confidence -> Mango
        item = {"type": "synced", "confidence": 0.15, "peak_ratio": 1.0, "method": "xcorr"}
        assert PremiereXMLGenerator._get_label_color(item) == "Mango"

        # Timestamp only -> Iris
        item = {"type": "synced", "confidence": 0.03, "method": "timestamp_only"}
        assert PremiereXMLGenerator._get_label_color(item) == "Iris"

        # Video only -> Rose
        item = {"type": "video_only"}
        assert PremiereXMLGenerator._get_label_color(item) == "Rose"

        # Audio only -> Lavender
        item = {"type": "audio_only"}
        assert PremiereXMLGenerator._get_label_color(item) == "Lavender"

    def test_labels_present_in_xml(self, generator, synced_item, tmp_path):
        """Generated XML clipitems should contain <labels><label2> elements."""
        timeline = [synced_item]
        output = tmp_path / "test_labels.xml"
        generator.generate(timeline, str(output))

        tree = ET.parse(str(output))
        root = tree.getroot()

        # Find label2 elements
        labels = root.findall(".//clipitem/labels/label2")
        assert len(labels) > 0, "No label elements found in XML"

        # At least one should be "Forest" (high confidence)
        label_texts = [l.text for l in labels if l.text]
        assert any("Forest" in t for t in label_texts), (
            f"Expected 'Forest' label for high-confidence sync, found: {label_texts}"
        )


# ---------------------------------------------------------------------------
# Frame Calculation
# ---------------------------------------------------------------------------

class TestFrameCalculation:
    """Test that frame-based calculations are correct."""

    def test_fr_method(self, generator):
        """The fr() method should convert seconds to frames correctly."""
        # At 29.97 fps, 1 second = ~30 frames (rounded)
        frames = generator.fr(1.0)
        assert frames == round(1.0 * 29.97)

        # 10 seconds
        frames = generator.fr(10.0)
        assert frames == round(10.0 * 29.97)

    def test_fr_zero(self, generator):
        """0 seconds = 0 frames."""
        assert generator.fr(0.0) == 0


# ---------------------------------------------------------------------------
# Timestamp Only Handling
# ---------------------------------------------------------------------------

class TestTimestampOnlyHandling:
    """Test that timestamp_only items don't place external audio."""

    def test_timestamp_only_no_external_audio(self, generator, timestamp_only_item, tmp_path):
        """timestamp_only items should NOT place external audio tracks in XML."""
        timeline = [timestamp_only_item]
        output = tmp_path / "test_ts_only.xml"
        generator.generate(timeline, str(output))

        tree = ET.parse(str(output))
        root = tree.getroot()

        # Count audio clipitems
        audio_clips = root.findall(".//audio/track/clipitem")
        # There should be camera audio but NOT external recorder audio
        # For timestamp_only, the external audio (ZOOM) should be skipped
        for clip in audio_clips:
            file_el = clip.find(".//file")
            if file_el is not None:
                name_el = file_el.find("name")
                if name_el is not None and name_el.text:
                    # Should NOT contain ZOOM audio for timestamp_only
                    assert "ZOOM" not in name_el.text.upper() or "Tr" not in name_el.text, (
                        f"External audio should not be placed for timestamp_only: {name_el.text}"
                    )
