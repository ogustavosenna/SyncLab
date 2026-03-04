"""
Server helper functions — pure utilities for the Flask backend.

Extracted from server.py during v1.3.0 refactoring.
Provides confidence badge computation, timeline serialization,
and Windows folder dialog.

Dependencies: pathlib, subprocess (for folder dialog only).
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Confidence badge
# ---------------------------------------------------------------------------

def compute_confidence_badge(item: dict) -> str:
    """Compute a confidence badge color for a timeline item.

    Parameters
    ----------
    item : dict
        Timeline item with ``confidence``, ``peak_ratio``, and
        ``method`` keys.

    Returns
    -------
    str
        ``"high"`` (green): conf >= 0.30 AND peak_ratio >= 2.0
        ``"medium"`` (yellow): conf >= 0.10 OR (conf >= 0.05 AND peak_ratio >= 1.5)
        ``"low"`` (red): everything else (including timestamp_only, vad_skip)
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


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

def serialize_value(value: Any) -> Any:
    """Recursively serialize a value, converting Paths to strings.

    Parameters
    ----------
    value
        Any value. Path objects are converted to strings.
        Dicts and lists are traversed recursively.

    Returns
    -------
    The serialized value.
    """
    if isinstance(value, Path):
        return str(value)
    elif isinstance(value, dict):
        return {k: serialize_value(v) for k, v in value.items()}
    elif isinstance(value, (list, tuple)):
        return [serialize_value(v) for v in value]
    return value


def serialize_timeline(timeline: List[dict]) -> List[Dict[str, Any]]:
    """Serialize timeline items for JSON transport.

    Converts Path objects to strings, adds confidence badges,
    and handles diagnostics recursively.

    Parameters
    ----------
    timeline : list of dict
        Timeline items from the matcher.

    Returns
    -------
    list of dict
        JSON-safe timeline items with ``badge`` keys added.
    """
    items = []
    for item in timeline:
        serialized = {}
        for key, value in item.items():
            if key == "diagnostics":
                # Include diagnostics but convert Paths
                serialized[key] = serialize_value(value)
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
        serialized["badge"] = compute_confidence_badge(item)
        items.append(serialized)
    return items


# ---------------------------------------------------------------------------
# Windows folder dialog
# ---------------------------------------------------------------------------

def win32_browse_folder() -> str:
    """Windows modern folder dialog (Explorer-style) via COM IFileDialog.

    Uses PowerShell with inline C# to create an IFileOpenDialog with
    FOS_PICKFOLDERS flag.  This gives the full Windows Explorer-style
    folder picker instead of the legacy FolderBrowserDialog tree view.
    Falls back to the basic dialog if COM compilation fails.

    Returns
    -------
    str
        Selected folder path, or empty string on cancel/error.
    """
    import subprocess as sp
    import base64

    from synclab.subprocess_utils import subprocess_hide_window

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
        logger.debug("Modern dialog error: %s", e)

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
        logger.debug("Fallback dialog error: %s", e)
        return ""
