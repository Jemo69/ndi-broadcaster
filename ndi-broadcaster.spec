# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build for NDI Broadcaster (Windows .exe + Linux binary)."""
from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
)

block_cipher = None

# cyndilib ships the NDI runtime as package binaries — make sure they travel.
# collect_submodules is REQUIRED: cyndilib's cython submodules import each
# other from compiled code (invisible to static analysis), so without this
# the frozen app fails with "No module named 'cyndilib.wrapper.common'".
cyndilib_binaries = []
cyndilib_datas = []
cyndilib_modules = ["cyndilib"]
try:
    cyndilib_binaries = collect_dynamic_libs("cyndilib")
    cyndilib_datas = collect_data_files("cyndilib")
    cyndilib_modules = collect_submodules("cyndilib")
except Exception:
    pass

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=cyndilib_binaries,
    datas=cyndilib_datas,
    hiddenimports=cyndilib_modules + ["mss", "PIL", "numpy"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=["pyi_rth_ndi.py"],
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
