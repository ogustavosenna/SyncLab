"""
SyncLab App - Flask Backend Server
REST API + WebSocket for real-time progress updates.
"""

import os
import sys
import json
import time
import shutil
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
from synclab.core.engine import SyncEngine
from synclab.core.matcher import SmartMatcher
from synclab.export.premiere_xml import PremiereXMLGenerator


def _get_base_path():
    """Get base path for bundled resources (dev vs PyInstaller frozen)."""
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        return Path(sys._MEIPASS) / "synclab" / "app"
    return Path(__file__).parent


def _win32_browse_folder():
    """Windows modern folder dialog (Explorer-style) via COM IFileDialog.

    Uses PowerShell with inline C# to create an IFileOpenDialog with
    FOS_PICKFOLDERS flag.  This gives the full Windows Explorer-style
    folder picker instead of the legacy FolderBrowserDialog tree view.
    Falls back to the basic dialog if COM compilation fails.

    Returns selected folder path as string, or empty string on cancel/error.
    """
    import subprocess as sp
    import base64

    # Modern Explorer-style dialog via COM IFileDialog + C# interop
    ps_code = r'''
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;

[ComImport, Guid("DC1C5A9C-E88A-4DDE-A5A1-60F82A20AEF7")]
class FileOpenDialogRCW { }

[ComImport, Guid("42F85136-DB7E-439C-85F1-E4075D135FC8"),
 InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IFileDialog {
    [PreserveSig] int Show(IntPtr hwndOwner);
    void SetFileTypes(uint cFileTypes, IntPtr rgFilterSpec);
    void SetFileTypeIndex(uint iFileType);
    void GetFileTypeIndex(out uint piFileType);
    void Advise(IntPtr pfde, out uint pdwCookie);
    void Unadvise(uint dwCookie);
    void SetOptions(uint fos);
    void GetOptions(out uint pfos);
    void SetDefaultFolder(IShellItem psi);
    void SetFolder(IShellItem psi);
    void GetFolder(out IShellItem ppsi);
    void GetCurrentSelection(out IShellItem ppsi);
    void SetFileName([MarshalAs(UnmanagedType.LPWStr)] string pszName);
    void GetFileName([MarshalAs(UnmanagedType.LPWStr)] out string pszName);
    void SetTitle([MarshalAs(UnmanagedType.LPWStr)] string pszTitle);
    void SetOkButtonLabel([MarshalAs(UnmanagedType.LPWStr)] string pszText);
    void SetFileNameLabel([MarshalAs(UnmanagedType.LPWStr)] string pszLabel);
    void GetResult(out IShellItem ppsi);
    void AddPlace(IShellItem psi, int fdap);
    void SetDefaultExtension([MarshalAs(UnmanagedType.LPWStr)] string pszDefaultExtension);
    void Close(int hr);
    void SetClientGuid(ref Guid guid);
    void ClearClientData();
    void SetFilter(IntPtr pFilter);
}

[ComImport, Guid("43826D1E-E718-42EE-BC55-A1E261C37BFE"),
 InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IShellItem {
    void BindToHandler(IntPtr pbc, ref Guid bhid, ref Guid riid, out IntPtr ppv);
    void GetParent(out IShellItem ppsi);
    void GetDisplayName(uint sigdnName, [MarshalAs(UnmanagedType.LPWStr)] out string ppszName);
    void GetAttributes(uint sfgaoMask, out uint psfgaoAttribs);
    void Compare(IShellItem psi, uint hint, out int piOrder);
}

public class FolderPicker {
    public static string Pick() {
        IFileDialog dialog = (IFileDialog)new FileOpenDialogRCW();
        try {
            uint options;
            dialog.GetOptions(out options);
            dialog.SetOptions(options | 0x20 | 0x40);
            dialog.SetTitle("Select Folder");
            int hr = dialog.Show(IntPtr.Zero);
            if (hr != 0) return "";
            IShellItem item;
            dialog.GetResult(out item);
            string path;
            item.GetDisplayName(0x80058000, out path);
            return path ?? "";
        }
        catch { return ""; }
        finally { Marshal.ReleaseComObject(dialog); }
    }
}
"@

Write-Output ([FolderPicker]::Pick())
'''

    try:
        encoded = base64.b64encode(ps_code.encode('utf-16-le')).decode('ascii')
        result = sp.run(
            ['powershell', '-NoProfile', '-NonInteractive',
             '-EncodedCommand', encoded],
            capture_output=True, text=True, timeout=120,
            **subprocess_hide_window(),
        )
        folder = result.stdout.strip()
        if folder:
            return folder
        # Empty output + returncode 0 means user cancelled
        if result.returncode == 0:
            return ""
        # Non-zero return code: fall through to fallback
    except Exception as e:
        print(f"[SyncLab] Modern dialog error: {e}")

    # Fallback: basic FolderBrowserDialog (tree-style)
    try:
        ps_fallback = (
            'Add-Type -AssemblyName System.Windows.Forms;'
            '$d = New-Object System.Windows.Forms.FolderBrowserDialog;'
            '$d.Description = "Select Folder";'
            '$d.ShowNewFolderButton = $true;'
            'if ($d.ShowDialog() -eq "OK") { Write-Output $d.SelectedPath }'
        )
        result = sp.run(
            ['powershell', '-NoProfile', '-NonInteractive',
             '-Command', ps_fallback],
            capture_output=True, text=True, timeout=120,
            **subprocess_hide_window(),
        )
        return result.stdout.strip()
    except Exception as e:
        print(f"[SyncLab] Fallback dialog error: {e}")
        return ""


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
        import sys

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


def _run_sync(state, socketio, output_dir):
    """Run synchronization in a background thread."""
    try:
        config = state["config"]
        videos = state["videos"]
        audio_groups = state["audio_groups"]

        total_videos = len(videos)
        total_audio = len(audio_groups)

        # Create temp directory
        temp_dir = Path(tempfile.mkdtemp(prefix="synclab_"))

        # Phase weight ranges for overall progress bar
        # Metadata + timestamp are fast (~4%), audio sync is the real work (~88%)
        PHASE_RANGES = {
            "metadata": (0, 2),
            "timestamp_calibration": (2, 4),
            "audio_sync": (4, 92),
            "brute_force": (92, 99),
            "done": (100, 100),
        }

        def emit_progress(event_type, data):
            """Transform matcher events into frontend-compatible format.

            OVERALL progress: steady climb from 0-100% across all phases.
            STEP progress: per-video indicator (frontend animates per-video
            cycling so the two bars show clearly different information).
            """
            if not state["syncing"]:
                raise InterruptedError("Sync cancelled by user")

            if event_type == "phase":
                # Matcher uses "name", frontend expects "phase"
                phase_name = data.get("name", "")
                low, _ = PHASE_RANGES.get(phase_name, (0, 0))
                socketio.emit("phase", {
                    "phase": phase_name,
                    "description": data.get("description", ""),
                    "percent": low,
                })

            elif event_type == "progress":
                vi = data.get("video_index", -1)
                phase = data.get("phase", "audio_sync")
                detail = data.get("detail", "")

                # --- OVERALL: phase-weighted total progress ---
                low, high = PHASE_RANGES.get(phase, (0, 100))

                if phase == "brute_force":
                    # Use brute-force-local index for accurate progress
                    bf_idx = data.get("bf_index", 0)
                    bf_total = data.get("bf_total", 1)
                    if bf_total > 0:
                        overall_pct = low + (high - low) * (bf_idx + 1) / bf_total
                    else:
                        overall_pct = low
                    overall_detail = (
                        f"Extra pass — Unassigned video "
                        f"{bf_idx + 1} of {bf_total}"
                    )
                elif vi >= 0 and total_videos > 0:
                    video_pct = (vi + 1) / total_videos
                    overall_pct = low + (high - low) * video_pct
                    if phase == "audio_sync":
                        overall_detail = (
                            f"Phase 3 — Video {vi + 1} of {total_videos}"
                        )
                    else:
                        overall_detail = detail
                else:
                    overall_pct = low
                    # Show phase name for metadata/timestamp phases
                    if phase == "metadata":
                        overall_detail = f"Phase 1 — {detail}"
                    elif phase == "timestamp_calibration":
                        overall_detail = f"Phase 2 — {detail}"
                    else:
                        overall_detail = detail

                # --- STEP: clean detail for per-video display ---
                # Remove "[X/Y] " prefix from matcher detail strings
                step_detail = detail
                if detail.startswith("[") and "] " in detail:
                    step_detail = detail.split("] ", 1)[1]

                socketio.emit("progress", {
                    "overall_percent": round(overall_pct),
                    "overall_detail": overall_detail,
                    "video_index": vi,
                    "step_detail": step_detail,
                })

            elif event_type == "match":
                # Add type field and normalize field names for frontend
                match_data = dict(data)
                method = match_data.get("method", "")
                conf = match_data.get("confidence", 0)
                if conf > 0 or "timestamp" in method:
                    match_data["type"] = "synced"
                else:
                    match_data["type"] = "video_only"
                match_data["video_name"] = match_data.pop("video", "")
                match_data["audio_name"] = match_data.pop("audio", "")
                # Add confidence badge (v1.1)
                match_data["badge"] = _compute_confidence_badge(match_data)
                socketio.emit("match", match_data)

            else:
                socketio.emit(event_type, data)

        # Emit start
        emit_progress("sync_started", {
            "total_videos": total_videos,
            "total_audio_groups": total_audio,
        })

        # Create engine and matcher
        engine = SyncEngine(config)
        matcher = SmartMatcher(engine, config)

        # Run matching
        timeline = matcher.match(
            videos,
            audio_groups,
            temp_dir,
            progress_callback=emit_progress,
        )

        # Count results
        synced_audio = sum(
            1 for item in timeline
            if item.get("type") == "synced"
            and item.get("method", "") != "timestamp_only"
        )
        synced_ts = sum(
            1 for item in timeline
            if item.get("type") == "synced"
            and item.get("method", "") == "timestamp_only"
        )
        video_only = sum(1 for item in timeline if item.get("type") == "video_only")
        audio_only = sum(1 for item in timeline if item.get("type") == "audio_only")

        # Serialize timeline for JSON
        serialized = _serialize_timeline(timeline)

        results = {
            "timeline": timeline,
            "summary": {
                "total_videos": total_videos,
                "total_audio_groups": total_audio,
                "synced_audio": synced_audio,
                "synced_timestamp": synced_ts,
                "video_only": video_only,
                "audio_only": audio_only,
            },
            "serialized": serialized,
        }

        state["results"] = results

        emit_progress("sync_complete", results["summary"])

    except InterruptedError:
        socketio.emit("sync_cancelled", {})
    except Exception as e:
        socketio.emit("sync_error", {"message": str(e)})
    finally:
        state["syncing"] = False
        # Clean up temp directory
        try:
            if temp_dir.exists():
                shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass


def _compute_confidence_badge(item):
    """Compute a confidence badge color for a timeline item.

    Returns:
        "high" (green): conf >= 0.30 AND peak_ratio >= 2.0
        "medium" (yellow): conf >= 0.10 OR (conf >= 0.05 AND peak_ratio >= 1.5)
        "low" (red): everything else (including timestamp_only, vad_skip)
    """
    conf = item.get("confidence", 0.0)
    peak_ratio = item.get("peak_ratio") or 0.0
    method = item.get("method", "")

    # Timestamp-only and non-audio methods are always low
    if method in ("timestamp_only", "timestamp_fallback", "n/a", "vad_skip"):
        return "low"

    if conf >= 0.30 and peak_ratio >= 2.0:
        return "high"
    if conf >= 0.10 or (conf >= 0.05 and peak_ratio >= 1.5):
        return "medium"
    return "low"


def _serialize_timeline(timeline):
    """Serialize timeline items for JSON transport."""
    items = []
    for item in timeline:
        serialized = {}
        for key, value in item.items():
            if key == "diagnostics":
                # Include diagnostics but convert Paths
                serialized[key] = _serialize_value(value)
            elif isinstance(value, Path):
                serialized[key] = str(value)
            elif isinstance(value, dict):
                serialized[key] = {
                    k: str(v) if isinstance(v, Path) else v
                    for k, v in value.items()
                }
            else:
                serialized[key] = value
        # Add confidence badge
        serialized["badge"] = _compute_confidence_badge(item)
        items.append(serialized)
    return items


def _serialize_value(value):
    """Recursively serialize a value, converting Paths to strings."""
    if isinstance(value, Path):
        return str(value)
    elif isinstance(value, dict):
        return {k: _serialize_value(v) for k, v in value.items()}
    elif isinstance(value, (list, tuple)):
        return [_serialize_value(v) for v in value]
    return value
