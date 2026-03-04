"""
SyncLab Settings Persistence
Saves user settings to %APPDATA%/SyncLab/settings.json on Windows.
Cross-platform: macOS (~/Library/Application Support), Linux (~/.config).
"""

import json
import os
import sys
from pathlib import Path

from synclab.config import DEFAULT_CONFIG

# Keys that are persisted but NOT part of DEFAULT_CONFIG
_UI_STATE_KEYS = ("last_export_dir", "last_video_dir", "last_audio_dir",
                  "last_video_dirs", "last_audio_dirs")


def get_settings_dir():
    """Return the platform-specific settings directory, creating it if needed."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA", str(Path.home()))
        settings_dir = Path(base) / "SyncLab"
    elif sys.platform == "darwin":
        settings_dir = Path.home() / "Library" / "Application Support" / "SyncLab"
    else:
        settings_dir = Path.home() / ".config" / "synclab"

    settings_dir.mkdir(parents=True, exist_ok=True)
    return settings_dir


def get_settings_path():
    """Return the full path to settings.json."""
    return get_settings_dir() / "settings.json"


def load_settings():
    """Load settings from disk, merged with DEFAULT_CONFIG.

    Merging strategy: start from DEFAULT_CONFIG, then overlay saved values.
    This means new defaults added in future versions automatically appear,
    while user customizations are preserved.

    Returns:
        Dict with merged configuration.
    """
    config = dict(DEFAULT_CONFIG)

    settings_path = get_settings_path()
    if settings_path.exists():
        try:
            with open(settings_path, "r", encoding="utf-8") as f:
                saved = json.load(f)

            # Only overlay keys that exist in DEFAULT_CONFIG
            # (prevents stale keys from old versions from accumulating)
            for key in DEFAULT_CONFIG:
                if key in saved:
                    config[key] = saved[key]

            # Also load UI-state keys that are NOT in DEFAULT_CONFIG
            for key in _UI_STATE_KEYS:
                if key in saved:
                    config[key] = saved[key]

        except (json.JSONDecodeError, OSError, KeyError) as e:
            print(f"[SyncLab] Settings load failed, using defaults: {e}")

    return config


def save_settings(config):
    """Save current settings to disk.

    Args:
        config: Dict of settings to persist.
    """
    settings_path = get_settings_path()
    try:
        # Filter to only save keys that are in DEFAULT_CONFIG or are UI-state keys
        saveable = {}
        for key in DEFAULT_CONFIG:
            if key in config:
                saveable[key] = config[key]

        # Also save UI-state keys
        for key in _UI_STATE_KEYS:
            if key in config:
                saveable[key] = config[key]

        with open(settings_path, "w", encoding="utf-8") as f:
            json.dump(saveable, f, indent=2, default=str)

    except OSError as e:
        print(f"[SyncLab] Settings save failed: {e}")
