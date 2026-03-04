"""
SyncLab Core - Media Information
FFprobe wrapper for extracting media metadata.
"""

import json
import subprocess
from pathlib import Path

from synclab.subprocess_utils import subprocess_hide_window


def get_media_info(file_path):
    """Extract media information using ffprobe.

    Args:
        file_path: Path to media file (video or audio).

    Returns:
        Dict with keys: path, name, duration, has_audio, video_streams,
        audio_streams, width, height, fps, sample_rate, channels,
        creation_time.
    """
    fp = Path(file_path)
    info = {
        "path": str(fp),
        "name": fp.name,
        "duration": 0.0,
        "has_audio": False,
        "video_streams": 0,
        "audio_streams": 0,
        "width": 0,
        "height": 0,
        "fps": 0.0,
        "sample_rate": 0,
        "channels": 0,
        "creation_time": "",
    }

    try:
        cmd = [
            "ffprobe", "-v", "quiet",
            "-print_format", "json",
            "-show_format", "-show_streams",
            str(fp),
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30, **subprocess_hide_window())
        if r.returncode != 0:
            return info

        data = json.loads(r.stdout)
        fmt = data.get("format", {})
        info["duration"] = float(fmt.get("duration", 0))

        tags = fmt.get("tags", {})
        info["creation_time"] = tags.get("creation_time", "")

        for stream in data.get("streams", []):
            codec_type = stream.get("codec_type", "")
            if codec_type == "video":
                info["video_streams"] += 1
                info["width"] = int(stream.get("width", 0))
                info["height"] = int(stream.get("height", 0))
                fps_str = stream.get("r_frame_rate", "0/1")
                if "/" in fps_str:
                    n, d = fps_str.split("/")
                    if int(d) > 0:
                        info["fps"] = float(n) / float(d)
            elif codec_type == "audio":
                info["audio_streams"] += 1
                info["has_audio"] = True
                info["sample_rate"] = int(stream.get("sample_rate", 0))
                info["channels"] = int(stream.get("channels", 0))

    except Exception:
        pass

    return info
