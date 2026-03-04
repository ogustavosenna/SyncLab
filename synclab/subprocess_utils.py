"""
SyncLab — Subprocess Utilities
Provides platform-aware kwargs to hide console windows on Windows.

When PyInstaller bundles with console=False, every subprocess.run() or
subprocess.Popen() call flashes a black CMD window on screen. Adding
CREATE_NO_WINDOW to creationflags prevents this.

Usage:
    from synclab.subprocess_utils import subprocess_hide_window
    subprocess.run(cmd, **subprocess_hide_window())
"""

import sys


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
