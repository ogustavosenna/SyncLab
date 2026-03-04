"""
SyncLab Logging Configuration

Centralized logging setup that replaces ad-hoc print() calls.
Output format mirrors the existing ``[SyncLab:module] message`` pattern
so terminal output is visually identical.

Usage::

    from synclab.logging_config import setup_logging
    setup_logging()  # Call once at app startup

Each module uses::

    import logging
    logger = logging.getLogger(__name__)
"""

import logging
import sys


def setup_logging(level=logging.DEBUG):
    """Configure the ``synclab`` logger hierarchy.

    Parameters
    ----------
    level : int
        Minimum log level (default: DEBUG so all messages appear).
        In production, callers can pass ``logging.INFO``.

    Notes
    -----
    Uses a custom formatter that translates ``__name__`` into a short
    label matching the previous print-based format.
    """
    root = logging.getLogger("synclab")

    # Avoid duplicate handlers when called multiple times
    if root.handlers:
        return root

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_SyncLabFormatter())
    root.setLevel(level)
    root.addHandler(handler)

    # Prevent propagation to the root logger (avoids double output)
    root.propagate = False

    return root


class _SyncLabFormatter(logging.Formatter):
    """Custom formatter producing ``[SyncLab:Module] message`` lines.

    Maps ``__name__`` values to short labels that match the pre-existing
    print output, ensuring terminal output is visually identical.
    """

    # Map full module names to short labels
    _LABEL_MAP = {
        "synclab.settings": "Settings",
        "synclab.app.server": "Server",
        "synclab.app.helpers": "Helpers",
        "synclab.app.main": "Main",
        "synclab.app.pywebview_patch": "Patch",
        "synclab.app.sync_runner": "SyncRunner",
    }

    def format(self, record):
        label = self._LABEL_MAP.get(record.name, record.name.split(".")[-1])
        return f"[SyncLab:{label}] {record.getMessage()}"
