"""
SyncLab — Subprocess Utilities
Provides platform-aware kwargs to hide console windows on Windows
and bundled ffmpeg/ffprobe path resolution.

When PyInstaller bundles with console=False, every subprocess.run() or
subprocess.Popen() call flashes a black CMD window on screen. Adding
CREATE_NO_WINDOW to creationflags prevents this.

Usage:
    from synclab.subprocess_utils import subprocess_hide_window, get_ffmpeg, get_ffprobe
    subprocess.run([get_ffmpeg(), ...], **subprocess_hide_window())
"""

import os
import shutil
import sys
from pathlib import Path


# Windows CREATE_NO_WINDOW flag
_CREATE_NO_WINDOW = 0x08000000


def subprocess_hide_window():
    """Return kwargs dict to pass to subprocess.run/Popen to hide console windows.

    On Windows: returns {'creationflags': CREATE_NO_WINDOW}
    On other platforms: returns empty dict (no-op)
    """
    if sys.platform == "win32":
        return {"creationflags": _CREATE_NO_WINDOW}
    return {}


def _bundled_dir() -> Path:
    """Return the directory where bundled binaries live.

    When running from a PyInstaller bundle, this is the _internal
    folder next to the .exe.  When running from source, returns None.
    """
    # PyInstaller sets sys._MEIPASS to the temp extraction dir,
    # but for --onedir mode the exe sits next to _internal/.
    # We check for an 'ffmpeg' folder inside _internal first.
    if getattr(sys, "frozen", False):
        # sys._MEIPASS points to dist/SyncLab/_internal
        return Path(sys._MEIPASS) / "ffmpeg"
    return None


def get_ffmpeg() -> str:
    """Return the path to the ffmpeg executable.

    Search order:
      1. Bundled ffmpeg inside PyInstaller package
      2. System PATH
    """
    bundled = _bundled_dir()
    if bundled is not None:
        exe = bundled / ("ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")
        if exe.exists():
            return str(exe)

    # Fallback: system PATH
    found = shutil.which("ffmpeg")
    if found:
        return found

    # Last resort: bare name (will fail with a clear error)
    return "ffmpeg"


def get_ffprobe() -> str:
    """Return the path to the ffprobe executable.

    Search order:
      1. Bundled ffprobe inside PyInstaller package
      2. System PATH
    """
    bundled = _bundled_dir()
    if bundled is not None:
        exe = bundled / ("ffprobe.exe" if sys.platform == "win32" else "ffprobe")
        if exe.exists():
            return str(exe)

    # Fallback: system PATH
    found = shutil.which("ffprobe")
    if found:
        return found

    return "ffprobe"
