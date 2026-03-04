"""
SyncLab App - Flask Backend Server
REST API + WebSocket for real-time progress updates.

v1.3.0 refactoring:
  - Helper functions extracted to helpers.py
    (compute_confidence_badge, serialize_timeline, serialize_value,
    win32_browse_folder)
  - Background sync runner extracted to sync_runner.py
  - This module retains create_app() with route definitions
"""

import os
import sys
import json
import time
import tempfile
import threading
import zipfile
from pathlib import Path

from flask import Flask, request, jsonify, send_from_directory
from flask_socketio import SocketIO

from synclab import __version__, __app_name__
from synclab.settings import load_settings, save_settings
from synclab.dependencies import check_ffmpeg, get_system_info
from synclab.subprocess_utils import subprocess_hide_window
from synclab.scanner.scanner import scan_folders
from synclab.export.premiere_xml import PremiereXMLGenerator
from synclab.app.helpers import (
    compute_confidence_badge,
    serialize_timeline,
    serialize_value,
    win32_browse_folder,
)
from synclab.app.sync_runner import run_sync

# Backward-compatible aliases (used by tests and external code)
_compute_confidence_badge = compute_confidence_badge
_serialize_timeline = serialize_timeline
_serialize_value = serialize_value
_win32_browse_folder = win32_browse_folder
_run_sync = run_sync


def _get_base_path():
    """Get base path for bundled resources (dev vs PyInstaller frozen)."""
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        return Path(sys._MEIPASS) / "synclab" / "app"
    return Path(__file__).parent


def create_app():
    """Create and configure the Flask application."""
    base_path = _get_base_path()
    app = Flask(
        __name__,
        static_folder=str(base_path / "static"),
        static_url_path="/static",
    )
    app.config["SECRET_KEY"] = "synclab-secret"

    socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

    # State
    state = {
        "config": load_settings(),
        "videos": [],
        "audio_groups": [],
        "results": None,
        "syncing": False,
        "job_thread": None,
    }

    # ----- Static Routes -----

    @app.route("/")
    def index():
        return send_from_directory(app.static_folder, "index.html")

    # ----- API Routes -----

    @app.route("/api/config", methods=["GET"])
    def get_current_config():
        return jsonify(state["config"])

    @app.route("/api/config", methods=["POST"])
    def update_config():
        data = request.get_json()
        if data:
            state["config"].update(data)
            save_settings(state["config"])
        return jsonify(state["config"])

    @app.route("/api/version")
    def get_version():
        return jsonify({"version": __version__, "app_name": __app_name__})

    @app.route("/api/check-dependencies")
    def check_dependencies():
        return jsonify(check_ffmpeg())

    @app.route("/api/export-support", methods=["POST"])
    def export_support():
        """Export a support package (ZIP) with XML, diagnostics, config, system info."""
        data = request.get_json() or {}
        output_dir = data.get("output_dir", "")

        if not output_dir:
            return jsonify({"error": "No output directory specified"}), 400

        timestamp = time.strftime("%Y%m%d_%H%M%S")
        zip_filename = f"SyncLab_Support_{timestamp}.zip"
        zip_path = Path(output_dir) / zip_filename

        try:
            with zipfile.ZipFile(str(zip_path), "w", zipfile.ZIP_DEFLATED) as zf:
                # 1. Config snapshot
                zf.writestr("config.json", json.dumps(state["config"], indent=2, default=str))

                # 2. System info + SyncLab version
                sys_info = get_system_info()
                sys_info["synclab_version"] = __version__
                zf.writestr("system_info.json", json.dumps(sys_info, indent=2, default=str))

                # 3. Diagnostics JSON (if results exist)
                if state["results"]:
                    diagnostics_data = []
                    for item in state["results"].get("serialized", []):
                        diag_entry = {
                            "video_name": item.get("video_name"),
                            "audio_name": item.get("audio_name"),
                            "type": item.get("type"),
                            "method": item.get("method"),
                            "offset": item.get("offset"),
                            "confidence": item.get("confidence"),
                            "peak_ratio": item.get("peak_ratio"),
                            "badge": item.get("badge"),
                            "source_folder": item.get("source_folder"),
                        }
                        if "diagnostics" in item:
                            diag_entry["diagnostics"] = item["diagnostics"]
                        diagnostics_data.append(diag_entry)
                    zf.writestr("diagnostics.json", json.dumps(diagnostics_data, indent=2, default=str))

                    # 4. Generate fresh XML
                    config = state["config"]
                    generator = PremiereXMLGenerator(
                        fps=config.get("premiere_fps", 29.97),
                        width=config.get("premiere_width", 1920),
                        height=config.get("premiere_height", 1080),
                        sample_rate=config.get("premiere_sample_rate", 48000),
                    )
                    tmp_xml = Path(tempfile.mktemp(suffix=".xml", prefix="synclab_support_"))
                    try:
                        generator.generate(
                            state["results"]["timeline"],
                            str(tmp_xml),
                            project_name="SyncLab_Support",
                        )
                        zf.write(str(tmp_xml), "SyncLab_Support.xml")
                    finally:
                        tmp_xml.unlink(missing_ok=True)

            return jsonify({"zip_path": str(zip_path), "filename": zip_filename})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/scan", methods=["POST"])
    def scan():
        """Scan video and audio folders."""
        data = request.get_json()
        video_folders = data.get("video_folders", [])
        audio_folders = data.get("audio_folders", [])

        if not video_folders and not audio_folders:
            return jsonify({"error": "No folders specified"}), 400

        config = state["config"]
        videos, audio_groups = scan_folders(
            video_folders,
            audio_folders,
            video_extensions=config.get("video_extensions"),
            audio_extensions=config.get("audio_extensions"),
            track_types=config.get("track_types"),
        )

        state["videos"] = videos
        state["audio_groups"] = audio_groups

        return jsonify({
            "videos": [
                {"name": v["name"], "path": str(v["path"])}
                for v in videos
            ],
            "audio_groups": [
                {
                    "name": g["zoom_dir"].name,
                    "path": str(g["zoom_dir"]),
                    "tracks": len(g["wav_files"]),
                }
                for g in audio_groups
            ],
            "total_videos": len(videos),
            "total_audio_groups": len(audio_groups),
        })

    @app.route("/api/sync", methods=["POST"])
    def start_sync():
        """Start synchronization in a background thread."""
        if state["syncing"]:
            return jsonify({"error": "Sync already in progress"}), 409

        if not state["videos"] and not state["audio_groups"]:
            return jsonify({"error": "No files scanned. Run /api/scan first."}), 400
        if not state["videos"]:
            return jsonify({"error": "No video files found."}), 400
        if not state["audio_groups"]:
            return jsonify({"error": "No audio groups found. Audio folder must contain ZOOM subfolders with WAV files."}), 400

        data = request.get_json() or {}
        output_dir = data.get("output_dir", "")

        state["syncing"] = True
        state["results"] = None

        thread = threading.Thread(
            target=_run_sync,
            args=(state, socketio, output_dir),
            daemon=True,
        )
        state["job_thread"] = thread
        thread.start()

        return jsonify({"status": "started"})

    @app.route("/api/status", methods=["GET"])
    def get_status():
        """Get sync status."""
        return jsonify({
            "syncing": state["syncing"],
            "has_results": state["results"] is not None,
        })

    @app.route("/api/results", methods=["GET"])
    def get_results():
        """Get sync results."""
        if state["results"] is None:
            return jsonify({"error": "No results available"}), 404
        return jsonify(state["results"])

    @app.route("/api/export", methods=["POST"])
    def export_xml():
        """Export timeline as Premiere Pro XML."""
        if state["results"] is None:
            return jsonify({"error": "No results to export"}), 404

        data = request.get_json() or {}
        output_dir = data.get("output_dir", "")
        project_name = data.get("project_name", "SyncLab")

        if not output_dir:
            # Default: same folder as first video
            if state["videos"]:
                output_dir = str(state["videos"][0]["path"].parent)
            else:
                output_dir = str(Path.home() / "Desktop")

        config = state["config"]
        generator = PremiereXMLGenerator(
            fps=config.get("premiere_fps", 29.97),
            width=config.get("premiere_width", 1920),
            height=config.get("premiere_height", 1080),
            sample_rate=config.get("premiere_sample_rate", 48000),
        )

        timestamp = time.strftime("%Y%m%d_%H%M%S")
        xml_filename = f"SyncLab_{project_name}_{timestamp}.xml"
        xml_path = Path(output_dir) / xml_filename

        try:
            generator.generate(
                state["results"]["timeline"],
                str(xml_path),
                project_name=project_name,
            )

            # Write diagnostics JSON alongside XML (v1.1)
            diag_filename = f"SyncLab_{project_name}_{timestamp}_diagnostics.json"
            diag_path = Path(output_dir) / diag_filename
            try:
                diagnostics_data = []
                for item in state["results"].get("serialized", []):
                    diag_entry = {
                        "video_name": item.get("video_name"),
                        "audio_name": item.get("audio_name"),
                        "type": item.get("type"),
                        "method": item.get("method"),
                        "offset": item.get("offset"),
                        "confidence": item.get("confidence"),
                        "peak_ratio": item.get("peak_ratio"),
                        "badge": item.get("badge"),
                        "source_folder": item.get("source_folder"),
                    }
                    if "diagnostics" in item:
                        diag_entry["diagnostics"] = item["diagnostics"]
                    diagnostics_data.append(diag_entry)

                with open(diag_path, "w", encoding="utf-8") as f:
                    json.dump(diagnostics_data, f, indent=2, default=str)
            except Exception as diag_err:
                print(f"[SyncLab] Diagnostics JSON write failed: {diag_err}")

            # Remember last export dir
            state["config"]["last_export_dir"] = output_dir
            save_settings(state["config"])

            return jsonify({
                "xml_path": str(xml_path),
                "filename": xml_filename,
                "diagnostics_path": str(diag_path),
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/open_folder", methods=["POST"])
    def open_folder():
        """Open a folder in the OS file explorer, selecting the file if given."""
        import subprocess as sp

        data = request.get_json() or {}
        file_path = data.get("path", "")

        if not file_path or not os.path.exists(file_path):
            return jsonify({"error": "Path not found"}), 404

        try:
            if sys.platform == "win32":
                # /select highlights the file in Explorer
                sp.Popen(["explorer", "/select,", file_path], **subprocess_hide_window())
            elif sys.platform == "darwin":
                sp.Popen(["open", "-R", file_path])
            else:
                sp.Popen(["xdg-open", str(Path(file_path).parent)])
            return jsonify({"status": "ok"})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/resolve_path", methods=["POST"])
    def resolve_path():
        """Resolve a filesystem path to a folder.

        If path is a directory, returns it as-is.
        If path is a file, returns its parent directory.
        Returns empty folder for paths that don't exist on disk.
        """
        data = request.get_json() or {}
        path = data.get("path", "")

        print(f"[SyncLab:Server] /api/resolve_path called with: {path!r}")

        if not path:
            return jsonify({"folder": "", "error": "empty path"})

        if not os.path.exists(path):
            print(f"[SyncLab:Server] Path does NOT exist: {path!r}")
            return jsonify({"folder": "", "error": f"path not found: {path}"})

        if os.path.isdir(path):
            print(f"[SyncLab:Server] Resolved to folder: {path!r}")
            return jsonify({"folder": path})

        # It's a file — return parent directory
        parent = str(Path(path).parent)
        print(f"[SyncLab:Server] Resolved to parent: {parent!r}")
        return jsonify({"folder": parent})

    @app.route("/api/debug_log", methods=["POST"])
    def debug_log():
        """Receive debug log messages from JS and print to terminal."""
        data = request.get_json() or {}
        level = data.get("level", "info")
        message = data.get("message", "")
        source = data.get("source", "JS")
        print(f"[SyncLab:{source}:{level}] {message}")
        return jsonify({"status": "ok"})

    @app.route("/api/browse", methods=["POST"])
    def browse_folder():
        """Open native folder browser dialog.

        On Windows: uses ctypes COM IFileDialog (works in PyInstaller).
        Fallback: spawns tkinter subprocess (dev mode only).
        Other OS: osascript (macOS) or zenity (Linux).
        """
        import subprocess as sp

        folder = ""

        if sys.platform == "win32":
            folder = _win32_browse_folder()
            # Fallback to tkinter subprocess if ctypes failed and not frozen
            if not folder and not getattr(sys, 'frozen', False):
                script = (
                    "import tkinter as tk\n"
                    "from tkinter import filedialog\n"
                    "root = tk.Tk()\n"
                    "root.withdraw()\n"
                    "root.wm_attributes('-topmost', True)\n"
                    "root.focus_force()\n"
                    "folder = filedialog.askdirectory(\n"
                    "    title='Select Folder',\n"
                    "    mustexist=True,\n"
                    "    parent=root,\n"
                    ")\n"
                    "root.destroy()\n"
                    "print(folder or '')\n"
                )
                try:
                    result = sp.run(
                        [sys.executable, "-c", script],
                        capture_output=True, text=True, timeout=120,
                        **subprocess_hide_window(),
                    )
                    folder = result.stdout.strip()
                except Exception:
                    folder = ""
        else:
            try:
                if sys.platform == "darwin":
                    result = sp.run(
                        ["osascript", "-e", 'POSIX path of (choose folder)'],
                        capture_output=True, text=True, timeout=120,
                    )
                    folder = result.stdout.strip()
                else:
                    result = sp.run(
                        ["zenity", "--file-selection", "--directory"],
                        capture_output=True, text=True, timeout=120,
                    )
                    folder = result.stdout.strip()
            except Exception:
                folder = ""

        if folder:
            return jsonify({"path": folder})
        return jsonify({"path": ""})

    # ----- WebSocket Events -----

    @socketio.on("connect")
    def on_connect():
        socketio.emit("connected", {"status": "ok"})

    @socketio.on("cancel_sync")
    def on_cancel():
        state["syncing"] = False

    return app, socketio
