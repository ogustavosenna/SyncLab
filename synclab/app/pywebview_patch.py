"""
SyncLab — PyWebView EdgeChromium Drag-and-Drop Patch

Isolates the monkey-patch for EdgeChromium's on_script_notify method
and the WinForms clipboard fallback into a dedicated module.

Usage:
    from synclab.app.pywebview_patch import apply_patch, try_winforms_drag_data
    apply_patch(enabled=True)
"""

import os
import traceback
from pathlib import Path


def apply_patch(enabled=True):
    """Apply the EdgeChromium monkey-patch for robust drag-and-drop.

    The issue: pywebview's on_script_notify checks for 'CoreWebView2File'
    in the type string, which may fail with pythonnet 3.1.0rc0 on Python 3.14.
    This patch makes the type check more permissive and adds diagnostic logging.

    Args:
        enabled: If False, skip patching entirely.
    """
    if not enabled:
        print("[SyncLab:Patch] EdgeChromium patch disabled by config")
        return

    try:
        from webview.platforms import edgechromium as ec

        # Find the function/method that handles FilesDropped
        original_handler = None
        target_class = None

        for name in dir(ec):
            obj = getattr(ec, name, None)
            if isinstance(obj, type) and hasattr(obj, 'on_script_notify'):
                target_class = obj
                original_handler = obj.on_script_notify
                break

        if not original_handler:
            print("[SyncLab:Patch] Could not find on_script_notify to patch")
            return

        def patched_on_script_notify(self, sender, args):
            """Patched version with logging and robust type checking."""
            try:
                return_value = args.get_WebMessageAsJson()
            except Exception:
                return_value = None

            if return_value and 'FilesDropped' in str(return_value):
                print(f"\n[SyncLab:Patch] === FilesDropped message received! ===")
                try:
                    from webview.dom import _dnd_state
                    print(f"[SyncLab:Patch] _dnd_state before: {_dnd_state}")
                except Exception as ex:
                    print(f"[SyncLab:Patch] Could not read _dnd_state: {ex}")

                try:
                    additional = args.get_AdditionalObjects()
                    print(f"[SyncLab:Patch] AdditionalObjects: {additional}")
                    print(f"[SyncLab:Patch] AdditionalObjects type: {type(additional)}")

                    if additional is not None:
                        for i, f in enumerate(list(additional)):
                            ftype = str(type(f))
                            print(f"[SyncLab:Patch] File[{i}] type: {ftype}")

                            # Try to get Path regardless of type name
                            try:
                                fpath = f.Path
                                fname = os.path.basename(fpath) if fpath else '?'
                                print(f"[SyncLab:Patch] File[{i}].Path: {fpath}")

                                # If the original type check would fail, manually add
                                if 'CoreWebView2File' not in ftype:
                                    print(f"[SyncLab:Patch] Type check would FAIL!")
                                    print(f"[SyncLab:Patch] Manually adding to _dnd_state")
                                    try:
                                        from webview.dom import _dnd_state
                                        _dnd_state['paths'].append((fname, fpath))
                                    except Exception as ex2:
                                        print(f"[SyncLab:Patch] Manual add failed: {ex2}")
                            except Exception as ex:
                                print(f"[SyncLab:Patch] File[{i}].Path error: {ex}")
                                # Try other attributes
                                try:
                                    attrs = [a for a in dir(f) if not a.startswith('_')]
                                    print(f"[SyncLab:Patch] File[{i}] attrs: {attrs}")
                                except Exception:
                                    pass
                    else:
                        print("[SyncLab:Patch] AdditionalObjects is None!")
                except Exception as ex:
                    print(f"[SyncLab:Patch] AdditionalObjects error: {ex}")
                    traceback.print_exc()

            # Call original handler
            return original_handler(self, sender, args)

        target_class.on_script_notify = patched_on_script_notify
        print(f"[SyncLab:Patch] Patched {target_class.__name__}.on_script_notify")

    except Exception as e:
        print(f"[SyncLab:Patch] EdgeChromium patching failed: {e}")
        traceback.print_exc()


def try_winforms_drag_data():
    """Try to get dropped file paths from WinForms/COM level.

    This is a last-resort fallback when pywebviewFullPath isn't available.
    Accesses the .NET clipboard/drag data directly via pythonnet.

    Returns:
        Folder path string, or empty string if not available.
    """
    try:
        import clr
        clr.AddReference('System.Windows.Forms')
        from System.Windows.Forms import Clipboard, DataFormats

        # Check if clipboard has file drop data (sometimes set during drag)
        if Clipboard.ContainsFileDropList():
            files = Clipboard.GetFileDropList()
            if files and files.Count > 0:
                path = str(files[0])
                if os.path.exists(path):
                    folder = path if os.path.isdir(path) else str(Path(path).parent)
                    print(f"[SyncLab:WinForms] Found path via Clipboard: {folder}")
                    return folder
    except Exception as e:
        print(f"[SyncLab:WinForms] Clipboard fallback failed: {e}")

    return ''
