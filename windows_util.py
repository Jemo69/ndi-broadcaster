"""Window + monitor enumeration for the NDI broadcaster.

Primary backend: `xwininfo -root -tree` (works on X11 and XWayland,
which is what you get on Wayland sessions that still run XWayland).
Fallback backend: `xdotool search`.

Monitors are enumerated via `mss` (always available once installed).
"""

from __future__ import annotations

import re
import shutil
import subprocess

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
    wins = list_windows_xwininfo()
    if wins:
        return wins
    return list_windows_xdotool()


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
