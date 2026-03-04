"""
SyncLab Dependency Checker
Validates that required external tools (FFmpeg, FFprobe) are available.
"""

import os
import platform
import subprocess
import sys

from synclab.subprocess_utils import subprocess_hide_window


def check_ffmpeg():
    """Check if FFmpeg and FFprobe are available on PATH.

    Returns:
        Dict with ffmpeg_ok, ffprobe_ok, ffmpeg_version, ffprobe_version,
        all_ok, and a human-readable message.
    """
    result = {
        "ffmpeg_ok": False,
        "ffprobe_ok": False,
        "ffmpeg_version": None,
        "ffprobe_version": None,
        "all_ok": False,
        "message": "",
    }

    for tool in ("ffmpeg", "ffprobe"):
        try:
            r = subprocess.run(
                [tool, "-version"],
                capture_output=True, text=True, timeout=10,
                **subprocess_hide_window(),
            )
            if r.returncode == 0:
                result[f"{tool}_ok"] = True
                first_line = r.stdout.split("\n")[0].strip()
                result[f"{tool}_version"] = first_line
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            pass

    result["all_ok"] = result["ffmpeg_ok"] and result["ffprobe_ok"]

    if result["all_ok"]:
        result["message"] = "FFmpeg and FFprobe are installed and working."
    elif not result["ffmpeg_ok"] and not result["ffprobe_ok"]:
        result["message"] = (
            "FFmpeg is not installed or not on PATH. "
            "SyncLab requires FFmpeg to extract and analyze audio. "
            "Download from https://ffmpeg.org/download.html"
        )
    elif not result["ffmpeg_ok"]:
        result["message"] = "FFmpeg is missing but FFprobe was found. Please reinstall FFmpeg."
    else:
        result["message"] = "FFprobe is missing but FFmpeg was found. Please reinstall FFmpeg."

    return result


def get_system_info():
    """Collect system information for the support package."""
    info = {
        "os": platform.platform(),
        "os_version": platform.version(),
        "architecture": platform.machine(),
        "python_version": sys.version,
        "cpu_count": os.cpu_count(),
        "ram_total_gb": "unknown",
        "ram_available_gb": "unknown",
    }

    # RAM (Windows)
    try:
        if sys.platform == "win32":
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(stat)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
            info["ram_total_gb"] = round(stat.ullTotalPhys / (1024 ** 3), 1)
            info["ram_available_gb"] = round(stat.ullAvailPhys / (1024 ** 3), 1)
    except Exception:
        pass

    # FFmpeg version
    ffmpeg_info = check_ffmpeg()
    info["ffmpeg_version"] = ffmpeg_info.get("ffmpeg_version", "not found")
    info["ffprobe_version"] = ffmpeg_info.get("ffprobe_version", "not found")

    return info
