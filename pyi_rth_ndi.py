"""PyInstaller runtime hook: make the NDI runtime findable in frozen builds.

cyndilib locates its DLL via importlib.resources (works in modern PyInstaller),
but Windows extension linkage + DLL search order are fragile in --onefile
mode. Belt and suspenders: add the extracted cyndilib bin dir to the DLL
search path and pre-load the NDI runtime when present. All failures are
ignored here — cyndilib's own import will report problems (surfaced in the
app's Diagnose dialog).
"""

import os
import sys

if hasattr(sys, "_MEIPASS") and sys.platform == "win32":
    base = sys._MEIPASS
    candidates = (
        os.path.join(base, "cyndilib", "wrapper", "bin"),
        os.path.join(base, "_internal", "cyndilib", "wrapper", "bin"),
    )
    for bin_dir in candidates:
        if not os.path.isdir(bin_dir):
            continue
        try:
            os.add_dll_directory(bin_dir)
        except Exception:
            pass
        dll = os.path.join(bin_dir, "Processing.NDI.Lib.x64.dll")
        if os.path.exists(dll):
            try:
                import ctypes

                ctypes.WinDLL(dll)
            except Exception:
                pass
        break
