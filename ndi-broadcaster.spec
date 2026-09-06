# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build for NDI Broadcaster (Windows .exe + Linux binary)."""
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

block_cipher = None

# cyndilib ships the NDI runtime as package binaries — make sure they travel.
cyndilib_binaries = []
cyndilib_datas = []
try:
    cyndilib_binaries = collect_dynamic_libs("cyndilib")
    cyndilib_datas = collect_data_files("cyndilib")
except Exception:
    pass

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=cyndilib_binaries,
    datas=cyndilib_datas,
    hiddenimports=["cyndilib", "mss", "PIL", "numpy"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="NDI-Broadcaster",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # GUI app: no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
