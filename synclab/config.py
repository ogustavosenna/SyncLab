"""
SyncLab Configuration
Default settings for audio-video synchronization.
"""

DEFAULT_CONFIG = {
    # Audio analysis
    "sample_rate": 8000,            # Analysis sample rate (Hz)
    "max_camera_sec": 60,           # Max camera audio for raw xcorr (seconds)
    "max_zoom_sec": 0,              # Max external audio (0 = no limit)
    "sync_window_sec": 30,          # Search window around timestamp (±seconds)
    "threshold": 0.05,              # Minimum confidence to accept match
    "bandpass_low": 200,            # Bandpass low frequency (Hz)
    "bandpass_high": 4000,          # Bandpass high frequency (Hz)
    "timestamp_tolerance_sec": 15,  # Tolerance for timestamp assignment (seconds)
    "timestamp_tolerance_max": 30,  # Expanded tolerance if <50% assigned (v1.2.1)
    "min_peak_ratio": 2.0,         # Min peak_ratio to accept match (v1.2.2)

    # Multi-slice Stage 1
    "multi_slice_enabled": True,    # Use 3 slices instead of first 60s
    "multi_slice_count": 3,         # Number of slices (beginning, middle, end)
    "multi_slice_duration": 20,     # Duration per slice in seconds

    # Voice Activity Detection (VAD)
    "vad_threshold": 0.05,          # speech_ratio below this = skip xcorr

    # Spectral whitening (reduces mic signature differences)
    "spectral_whiten": True,        # Flattens frequency response across mics (v1.2.1)

    # PyWebView platform patch
    "pywebview_patch_enabled": True, # Monkey-patch for EdgeChromium DnD

    # File discovery
    "video_extensions": [".mov", ".mp4", ".mxf", ".avi"],
    "audio_extensions": [".wav"],
    "track_types": ["_Tr1", "_Tr2", "_Tr3", "_Tr4", "_LR"],

    # Premiere Pro export
    "premiere_fps": 29.97,
    "premiere_width": 1920,
    "premiere_height": 1080,
    "premiere_sample_rate": 48000,
}


def get_config(**overrides):
    """Return a config dict with optional overrides."""
    config = dict(DEFAULT_CONFIG)
    config.update(overrides)
    return config
