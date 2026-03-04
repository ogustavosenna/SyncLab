# -*- mode: python ; coding: utf-8 -*-
"""
SyncLab PyInstaller Spec — onedir mode
Produces: dist/SyncLab/SyncLab.exe
"""

import os
import sys

block_cipher = None

a = Analysis(
    ['run.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('synclab/app/static', 'synclab/app/static'),
    ],
    hiddenimports=[
        # scipy submodules (used in core/audio.py and core/engine.py)
        'scipy.signal',
        'scipy.io.wavfile',
        'scipy.fft',
        'scipy.fft._pocketfft',
        # flask-socketio async driver
        'engineio.async_drivers.threading',
        # ensure flask_socketio internals are found
        'flask_socketio',
        'engineio',
        'socketio',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter',
        'matplotlib',
        'PIL',
        'IPython',
        'notebook',
        'pytest',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='SyncLab',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    icon='synclab/app/static/img/icon.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='SyncLab',
)
