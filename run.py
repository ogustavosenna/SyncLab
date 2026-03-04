#!/usr/bin/env python3
"""
SyncLab — Desktop Application Entry Point

Default: Launches native PyWebView window with drag-and-drop support.
         Use --browser flag for development in Chrome.
"""

import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))


def main():
    if "--browser" in sys.argv:
        # ---- Development mode: Flask + Chrome browser ----
        import webbrowser
        import threading
        from synclab import __version__
        from synclab.app.server import create_app

        app, socketio = create_app()
        port = int(os.environ.get("PORT", 5789))
        url = f"http://127.0.0.1:{port}"

        print(f"\n  SyncLab v{__version__} (browser mode)")
        print(f"  Running at: {url}")
        print(f"  Press Ctrl+C to stop\n")

        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
        socketio.run(
            app,
            host="127.0.0.1",
            port=port,
            debug=True,
            use_reloader=False,
            allow_unsafe_werkzeug=True,
        )
    else:
        # ---- Desktop mode: PyWebView native window ----
        from synclab.app.main import main as desktop_main
        desktop_main()


if __name__ == "__main__":
    main()
