"""Window + monitor enumeration for the NDI broadcaster.

Primary backend: `xwininfo -root -tree` (works on X11 and XWayland,
which is what you get on Wayland sessions that still run XWayland).
Fallback backend: `xdotool search`.

Monitors are enumerated via `mss` (always available once installed).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys

XWININFO_RE = re.compile(
    r'(0x[0-9a-fA-F]+)\s+"([^"]*)".*?(\d+)x(\d+)\+(-?\d+)\+(-?\d+)'
)
CLASS_RE = re.compile(r'\("([^"]*)"\s+"([^"]*)"\)')

MIN_W, MIN_H = 120, 80


def _run(cmd: list[str], timeout: float = 4.0) -> str:
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout
        )
        return out.stdout if out.returncode == 0 else ""
    except Exception:
        return ""


def list_windows_xwininfo() -> list[dict]:
    """Parse `xwininfo -root -tree` into window dicts."""
    if not shutil.which("xwininfo"):
        return []
    raw = _run(["xwininfo", "-root", "-tree"])
    if not raw:
        return []
    wins: list[dict] = []
    for line in raw.splitlines():
        m = XWININFO_RE.search(line)
        if not m:
            continue
        wid, title, w, h, x, y = (
            m.group(1), m.group(2).strip(),
            int(m.group(3)), int(m.group(4)),
            int(m.group(5)), int(m.group(6)),
        )
        if w < MIN_W or h < MIN_H:
            continue
        if x <= -8000 or y <= -8000:
            continue  # offscreen helper (clipboard etc.)
        if not title and (w < 200 or h < 150):
            continue
        cm = CLASS_RE.search(line)
        app = ""
        if cm:
            app = cm.group(2) or cm.group(1) or ""
        wins.append({
            "id": wid,
            "title": title or f"Untitled ({wid})",
            "app": app or "unknown",
            "x": x, "y": y, "w": w, "h": h,
        })
    # Biggest first — the window you want is rarely a 120x80 helper.
    wins.sort(key=lambda d: d["w"] * d["h"], reverse=True)
    return wins


def list_windows_xdotool() -> list[dict]:
    """Fallback enumerator using xdotool (X11/XWayland only)."""
    if not shutil.which("xdotool"):
        return []
    raw = _run(["xdotool", "search", "--onlyvisible", "--name", ""])
    if not raw.strip():
        return []
    wins: list[dict] = []
    for wid in raw.split():
        name = _run(["xdotool", "getwindowname", wid]).strip()
        geo = _run(["xdotool", "getwindowgeometry", "--shell", wid])
        vals: dict[str, int] = {}
        for line in geo.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                try:
                    vals[k.strip()] = int(v.strip())
                except ValueError:
                    pass
        w, h = vals.get("WIDTH", 0), vals.get("HEIGHT", 0)
        if w < MIN_W or h < MIN_H:
            continue
        wins.append({
            "id": wid,
            "title": name or f"Window {wid}",
            "app": "unknown",
            "x": vals.get("X", 0), "y": vals.get("Y", 0),
            "w": w, "h": h,
        })
    wins.sort(key=lambda d: d["w"] * d["h"], reverse=True)
    return wins


def list_windows() -> list[dict]:
    if sys.platform == "win32":
        return list_windows_windows()
    wins = list_windows_xwininfo()
    if wins:
        return wins
    return list_windows_xdotool()


def list_windows_windows() -> list[dict]:
    """Enumerate top-level windows on Windows via ctypes (no extra deps).

    Returns the same dicts as the X11 backends (id/title/app/x/y/w/h) so the
    rest of the app works unchanged. Coordinates are screen pixels for mss.
    """
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [EnumWindowsProc, wintypes.LPARAM]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    own_pid = os.getpid()
    found: list[dict] = []

    def exe_of(pid: int) -> str:
        try:
            h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not h:
                return "unknown"
            try:
                buf = ctypes.create_unicode_buffer(260)
                size = wintypes.DWORD(260)
                if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                    return (buf.value.rsplit("\\", 1)[-1] or "unknown").lower()
            finally:
                kernel32.CloseHandle(h)
        except Exception:
            pass
        return "unknown"

    def cb(hwnd, _lparam):
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value.strip()
            if not title:
                return True
            rect = wintypes.RECT()
            if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                return True
            w, h = rect.right - rect.left, rect.bottom - rect.top
            if w < MIN_W or h < MIN_H:
                return True
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == own_pid:
                return True  # hide our own broadcaster window
            found.append({
                "id": str(hwnd),
                "title": title,
                "app": exe_of(pid.value),
                "x": rect.left, "y": rect.top, "w": w, "h": h,
            })
        except Exception:
            pass
        return True

    try:
        user32.EnumWindows(EnumWindowsProc(cb), 0)
    except Exception:
        return []
    found.sort(key=lambda d: d["w"] * d["h"], reverse=True)
    return found


def list_monitors() -> list[dict]:
    """Return [{'index': i, 'x','y','w','h', 'label': ...}]."""
    try:
        import mss
    except ImportError:
        return []
    try:
        with mss.mss() as sct:
            mons = []
            for i, m in enumerate(sct.monitors[1:], start=1):
                mons.append({
                    "index": i,
                    "x": m["left"], "y": m["top"],
                    "w": m["width"], "h": m["height"],
                    "label": f"Monitor {i} — {m['width']}x{m['height']}",
                })
            return mons
    except Exception:
        return []
