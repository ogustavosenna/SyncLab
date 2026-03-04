"""
SyncLab Desktop Application
PyWebView 6.x wrapper for the Flask backend.

DRAG-AND-DROP ARCHITECTURE:

  The key insight is that PyWebView's pywebviewFullPath injection happens
  through its internal JS bridge, NOT through standard DOM addEventListener.
  When we register handlers via pywebview's DOM API (element.events.drop),
  the bridge intercepts the drop event, extracts file paths via WebView2's
  CoreWebView2File objects, and enriches the event dict with pywebviewFullPath.

  Architecture:
    1. app.js setupCardDrop provides visual feedback (dragenter/dragover/dragleave)
       and in PyWebView mode does NOT call stopPropagation or handle paths.
    2. Python registers drop handlers on #videoCard and #audioCard via
       pywebview's DOM API (element.events.drop += handler).
    3. PyWebView bridge intercepts the drop, extracts native file paths,
       and calls the Python handler with pywebviewFullPath populated.
    4. Python resolves the path and calls evaluate_js(handleNativeDrop(...))
       to update the UI.
    5. Fallback: if pywebviewFullPath is missing, checks _dnd_state directly,
       or tries WinForms-level DragDrop interception.

  Browser mode: app.js handles everything (path strategies + Browse Folder).

All other user actions (browse, export, open folder, resolve path)
go through Flask HTTP endpoints to avoid GUI thread deadlocks.
"""

import json
import logging
import os
import sys
import time
import threading
import traceback
from pathlib import Path

import webview

from synclab.app.server import create_app
from synclab.app.pywebview_patch import apply_patch, try_winforms_drag_data
from synclab.settings import load_settings

logger = logging.getLogger(__name__)


class Api:
    """Python-to-JavaScript bridge for PyWebView.

    Provides methods callable from JS via window.pywebview.api.*
    """

    def ping(self):
        """Health check — confirms the bridge is alive."""
        return "pong"

    def on_files_dropped(self, paths_json):
        """Fallback: called from JS when pywebview DOM bridge fails.

        JS sends us raw drop event data, and we resolve paths on the
        Python side where we have full filesystem access.

        Args:
            paths_json: JSON string with list of path candidates.
        Returns:
            JSON string with resolved folder path, or empty string.
        """
        try:
            candidates = json.loads(paths_json) if isinstance(paths_json, str) else paths_json
        except (json.JSONDecodeError, TypeError):
            candidates = []

        for path in candidates:
            if not path or not isinstance(path, str):
                continue
            path = path.strip()

            if path.startswith("file:///"):
                from urllib.parse import unquote
                path = unquote(path[8:])

            if os.path.exists(path):
                if os.path.isdir(path):
                    return json.dumps({"folder": path})
                else:
                    return json.dumps({"folder": str(Path(path).parent)})

        return json.dumps({"folder": ""})


# --------------------------------------------------------------------------
# JS injected after page load:
#   - handleNativeDrop(target, folder): called by Python to update UI
#   - Debug logging for drop events
# --------------------------------------------------------------------------

INJECT_JS = r"""
(function() {
    console.log('[SyncLab:PyWebView] Injecting native drop support...');

    // Global function: Python calls this via evaluate_js to update the UI
    window.handleNativeDrop = function(target, folder) {
        console.log('[SyncLab:NativeDrop] target=' + target + ' folder=' + folder);

        // Clear pending-drop timeout (if any)
        window._synclab_pending_drop = null;

        var el;
        if (target === 'audio') {
            el = document.getElementById('audioPathInput');
        } else {
            el = document.getElementById('videoPathInput');
        }

        if (typeof addPath === 'function') {
            addPath(el, folder);
        } else if (typeof setPath === 'function') {
            setPath(el, folder);
        } else {
            // Direct DOM update as fallback
            el.innerHTML = '<span class="path-text">' + folder.replace(/</g, '&lt;') + '</span>';
            el.dataset.path = folder;
        }
        if (typeof toast === 'function') toast('success', 'Folder loaded');
    };

    // Helper: send debug logs to Python terminal via Flask
    window._synclab_log = function(msg) {
        console.log('[SyncLab:Drop] ' + msg);
        fetch('/api/debug_log', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ level: 'info', source: 'Drop', message: msg }),
        }).catch(function() {});
    };

    console.log('[SyncLab:PyWebView] Native drop support ready');
})();
"""


def _get_icon_path():
    """Resolve path to app icon for dev and frozen (PyInstaller) mode."""
    if getattr(sys, 'frozen', False):
        base = Path(sys._MEIPASS) / "synclab" / "app" / "static" / "img"
    else:
        base = Path(__file__).parent / "static" / "img"
    ico = base / "icon.ico"
    png = base / "icon.png"
    if ico.exists():
        return str(ico)
    if png.exists():
        return str(png)
    return None


def main():
    """Launch SyncLab desktop application."""
    app, socketio = create_app()

    # Patch EdgeChromium for robust DnD + diagnostics (v1.1: isolated module)
    config = load_settings()
    apply_patch(enabled=config.get("pywebview_patch_enabled", True))

    # Start Flask+SocketIO in a background thread
    server_thread = threading.Thread(
        target=lambda: socketio.run(
            app,
            host="127.0.0.1",
            port=5789,
            debug=False,
            use_reloader=False,
            allow_unsafe_werkzeug=True,
        ),
        daemon=True,
    )
    server_thread.start()

    # Wait briefly for the server to start
    time.sleep(0.5)

    # API bridge
    api = Api()

    # Resolve icon path for window (dev vs frozen mode)
    icon_path = _get_icon_path()

    # Create PyWebView window pointing at the Flask server.
    # CRITICAL: easy_drag=False is REQUIRED for drag-and-drop to work.
    window = webview.create_window(
        title="SyncLab",
        url="http://127.0.0.1:5789",
        js_api=api,
        width=1100,
        height=800,
        min_size=(900, 600),
        text_select=False,
        easy_drag=False,
    )

    def on_loaded():
        """Called when the page finishes loading.

        1. Injects handleNativeDrop() JS function
        2. Registers Python drop handlers on #videoCard and #audioCard
           via PyWebView's DOM API (which triggers pywebviewFullPath injection)
        """
        logger.info("Page loaded — setting up drag-and-drop...")

        # Step 1: Inject JS helper functions
        try:
            window.evaluate_js(INJECT_JS)
            logger.debug("JS helpers injected (handleNativeDrop, _synclab_log)")
        except Exception as e:
            logger.warning("JS injection error: %s", e)

        # Step 2: Register Python drop handlers via pywebview DOM API
        # This is THE mechanism that triggers pywebviewFullPath injection:
        #   element.events.drop → pywebview bridge → FilesDropped message →
        #   CoreWebView2File.Path → _dnd_state → pywebviewFullPath in event dict
        # NOTE: Use window.dom.get_element() — NOT doc.query_selector()
        try:
            video_card = window.dom.get_element('#videoCard')
            audio_card = window.dom.get_element('#audioCard')

            if video_card:
                video_card.events.drop += _make_drop_handler(window, 'video')
                logger.info("Python drop handler registered on #videoCard")
            else:
                logger.warning("#videoCard not found in DOM")

            if audio_card:
                audio_card.events.drop += _make_drop_handler(window, 'audio')
                logger.info("Python drop handler registered on #audioCard")
            else:
                logger.warning("#audioCard not found in DOM")

            # Check _dnd_state to verify listeners were registered
            try:
                from webview.dom import _dnd_state
                logger.debug("_dnd_state after registration: %s", _dnd_state)
            except Exception:
                pass

        except Exception as e:
            logger.warning("DOM handler registration failed: %s", e)
            logger.debug(traceback.format_exc())
            logger.info("Will rely on JS-only drop handlers (app.js)")

    # Register the loaded callback
    window.events.loaded += on_loaded

    # Start the GUI event loop (blocks until window is closed)
    debug_mode = "--debug" in sys.argv
    logger.info("Starting PyWebView (debug=%s)...", debug_mode)
    start_kwargs = dict(debug=debug_mode)
    if icon_path:
        start_kwargs["icon"] = icon_path
    webview.start(**start_kwargs)


def _make_drop_handler(window, target_type):
    """Create a Python drop handler for a specific card.

    This handler is registered via pywebview's DOM API, which means:
    - PyWebView's bridge intercepts the drop event
    - It extracts file paths from WebView2's CoreWebView2File objects
    - It enriches the event dict with pywebviewFullPath
    - Then calls this handler with the enriched data

    Args:
        window: PyWebView window instance (for evaluate_js)
        target_type: 'video' or 'audio'
    """
    def handler(e):
        logger.debug("=== DROP on %s card ===", target_type)

        folder = ''

        # Strategy 1: Read pywebviewFullPath from enriched event
        if isinstance(e, dict):
            dt = e.get('dataTransfer', {})
            files = dt.get('files', [])
            logger.debug("Event has %d file(s)", len(files))

            for i, f in enumerate(files):
                if isinstance(f, dict):
                    keys = list(f.keys())
                    name = f.get('name', '?')
                    path = f.get('pywebviewFullPath', '')
                    logger.debug("file[%d]: name=%r, pywebviewFullPath=%r, keys=%s",
                                 i, name, path, keys)

                    if path:
                        if os.path.exists(path):
                            folder = path if os.path.isdir(path) else str(Path(path).parent)
                            logger.debug("Resolved via pywebviewFullPath: %s", folder)
                            break
                        else:
                            logger.debug("pywebviewFullPath does not exist: %r", path)
                else:
                    logger.debug("file[%d] is not a dict: %s", i, type(f))
        else:
            logger.debug("Event is not a dict: %s", type(e))

        # Strategy 2: Check _dnd_state directly (bypass pywebviewFullPath injection)
        if not folder:
            try:
                from webview.dom import _dnd_state
                paths = _dnd_state.get('paths', [])
                logger.debug("_dnd_state paths: %s", paths)

                if paths:
                    for entry in paths:
                        if isinstance(entry, (list, tuple)) and len(entry) >= 2:
                            candidate = str(entry[1])
                        else:
                            candidate = str(entry)

                        if os.path.exists(candidate):
                            folder = candidate if os.path.isdir(candidate) else str(Path(candidate).parent)
                            logger.debug("Resolved via _dnd_state: %s", folder)
                            break
            except ImportError:
                logger.debug("Could not import _dnd_state")
            except Exception as ex:
                logger.debug("_dnd_state check failed: %s", ex)

        # Strategy 3: Try WinForms-level DragDrop data
        if not folder:
            folder = try_winforms_drag_data()

        # Push result to JS
        if folder:
            logger.info("OK - Pushing folder to JS: %s", folder)
            try:
                js = f"handleNativeDrop({json.dumps(target_type)}, {json.dumps(folder)})"
                window.evaluate_js(js)
            except Exception as ex:
                logger.warning("evaluate_js error: %s", ex)
        else:
            logger.warning("FAIL - No folder resolved from drop event")
            # Notify JS that native drop failed (JS can show warning)
            try:
                window.evaluate_js(
                    "window._synclab_pending_drop = null;"
                    "if(typeof toast === 'function') "
                    "toast('warning', 'Could not read folder path. Try Browse Folder.');"
                )
            except Exception:
                pass

    return handler


if __name__ == "__main__":
    from synclab.logging_config import setup_logging
    setup_logging()
    main()
