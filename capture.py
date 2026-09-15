"""Background capture engine: grabs a screen region (or test pattern)
at a target FPS, feeds preview frames to the UI and BGRA frames to NDI.
"""

from __future__ import annotations

import queue
import shutil
import subprocess
import sys
import threading
import time

from ndi_sender import NdiSender

try:
    import numpy as np
    HAVE_NUMPY = True
except ImportError:
    HAVE_NUMPY = False

try:
    from PIL import Image
    HAVE_PIL = True
except ImportError:
    HAVE_PIL = False


def compute_output_size(src_w: int, src_h: int, scale: str) -> tuple[int, int]:
    """Map a scale preset to an output size, preserving aspect ratio."""
    presets = {"1080p": 1080, "720p": 720, "540p": 540, "360p": 360}
    if scale not in presets or src_h <= 0:
        return (max(2, src_w), max(2, src_h))
    target_h = presets[scale]
    if src_h <= target_h:
        return (src_w, src_h)
    w = max(2, round(src_w * target_h / src_h))
    w -= w % 2  # keep even for NDI / codecs
    return (w, target_h)


# -- mouse cursor overlay -------------------------------------------------
# mss grabs the screen pixels but never includes the mouse pointer, so when
# "show cursor" is on we paint a small arrow at the live pointer position.
_X11_STATE: dict = {"init": False, "ok": False, "lib": None,
                    "display": None, "root": 0}
_XDOTOOL: str | None = None
_XDOTOOL_CHECKED = False


def _init_x11() -> bool:
    """One-time X11 setup via ctypes (no extra deps). False on Wayland/etc."""
    if _X11_STATE["init"]:
        return _X11_STATE["ok"]
    _X11_STATE["init"] = True
    if not sys.platform.startswith("linux"):
        return False
    try:
        import ctypes
        from ctypes import c_int, c_uint, c_ulong  # noqa: F841
        lib = ctypes.CDLL("libX11.so.6")
        lib.XOpenDisplay.restype = ctypes.c_void_p
        lib.XOpenDisplay.argtypes = [ctypes.c_char_p]
        lib.XDefaultRootWindow.restype = c_ulong
        lib.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        lib.XQueryPointer.restype = c_int
        lib.XQueryPointer.argtypes = [ctypes.c_void_p, c_ulong,
                                      ctypes.POINTER(c_ulong), ctypes.POINTER(c_ulong),
                                      ctypes.POINTER(c_int), ctypes.POINTER(c_int),
                                      ctypes.POINTER(c_int), ctypes.POINTER(c_int),
                                      ctypes.POINTER(c_uint)]
        disp = lib.XOpenDisplay(None)
        if not disp:
            return False
        root = lib.XDefaultRootWindow(disp)
        _X11_STATE.update({"ok": True, "lib": lib,
                           "display": disp, "root": root})
        return True
    except Exception:
        _X11_STATE["ok"] = False
        return False


def get_cursor_pos() -> tuple[int, int] | None:
    """Live pointer position in screen pixels, or None if unavailable."""
    # Windows: cheap, dependency-free.
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes
            pt = wintypes.POINT()
            if ctypes.windll.user32.GetCursorPos(ctypes.byref(pt)):
                return (int(pt.x), int(pt.y))
        except Exception:
            return None
        return None
    # Linux: fast X11 path, xdotool fallback, None on pure Wayland.
    if sys.platform.startswith("linux"):
        if _init_x11():
            try:
                import ctypes
                from ctypes import c_int, c_uint, c_ulong, byref
                lib = _X11_STATE["lib"]
                disp = _X11_STATE["display"]
                root = _X11_STATE["root"]
                rr, cr = c_ulong(), c_ulong()
                rx, ry, wx, wy = c_int(), c_int(), c_int(), c_int()
                mask = c_uint()
                ok = lib.XQueryPointer(disp, root, byref(rr), byref(cr),
                                       byref(rx), byref(ry),
                                       byref(wx), byref(wy), byref(mask))
                if ok:
                    return (int(rx.value), int(ry.value))
            except Exception:
                pass
        global _XDOTOOL, _XDOTOOL_CHECKED
        if not _XDOTOOL_CHECKED:
            _XDOTOOL_CHECKED = True
            _XDOTOOL = shutil.which("xdotool")
        if _XDOTOOL:
            try:
                out = subprocess.run(
                    [_XDOTOOL, "getmouselocation", "--shell"],
                    capture_output=True, text=True, timeout=0.5)
                if out.returncode == 0:
                    vals: dict[str, int] = {}
                    for line in out.stdout.splitlines():
                        if "=" in line:
                            k, v = line.split("=", 1)
                            try:
                                vals[k.strip()] = int(v.strip())
                            except ValueError:
                                pass
                    if "X" in vals and "Y" in vals:
                        return (vals["X"], vals["Y"])
            except Exception:
                pass
        return None
    # macOS / others: not implemented without extra deps.
    return None


def draw_cursor_overlay(img, x: float, y: float) -> None:
    """Paint a white-arrow/black-outline cursor, tip at (x, y). In place."""
    try:
        from PIL import ImageDraw
    except ImportError:
        return
    try:
        out_h = img.size[1]
    except Exception:
        return
    s = max(1.0, out_h / 720.0)
    # Classic pointer shape, tip at (0, 0).
    base = [(0, 0), (0, 17), (4.2, 12.6), (6.8, 18),
            (9.0, 16.8), (6.4, 11.4), (11.2, 11.2)]
    pts = [(x + px * s, y + py * s) for px, py in base]
    d = ImageDraw.Draw(img)
    try:
        d.polygon(pts, fill="white", outline="black",
                  width=max(1, int(round(s))))
    except TypeError:
        # Older Pillow without width= — fill then thin outline.
        d.polygon(pts, fill="white", outline="black")


def make_test_pattern(w: int, h: int, t: float):
    """Animated color-bars test pattern as a PIL image (or None)."""
    if not (HAVE_PIL and HAVE_NUMPY):
        return None
    from PIL import Image as _Image
    import numpy as _np
    palette = _np.array([
        [255, 77, 94], [245, 169, 66], [247, 233, 72], [46, 204, 113],
        [0, 200, 255], [108, 92, 231], [232, 237, 242],
    ], dtype=_np.uint8)
    n = len(palette)
    xs = (_np.arange(w) * n // max(1, w)).clip(0, n - 1)
    img_arr = _np.empty((h, w, 3), dtype=_np.uint8)
    img_arr[:, :] = palette[xs][None, :, :]
    # Moving bright sweep
    sweep = int((t * w * 0.5) % (w + 200)) - 100
    x0, x1 = max(0, sweep - 30), min(w, sweep + 30)
    if x1 > x0:
        img_arr[:, x0:x1] = _np.minimum(
            img_arr[:, x0:x1].astype(_np.int16) + 60, 255).astype(_np.uint8)
    # Footer bar
    footer_h = max(18, h // 12)
    img_arr[h - footer_h:, :] = (14, 17, 22)
    return _Image.fromarray(img_arr, "RGB")


class CaptureEngine(threading.Thread):
    """Capture loop running in its own thread."""

    def __init__(
        self,
        source: dict,
        fps: int,
        scale: str,
        ndi_name: str,
        sender: NdiSender,
        preview_queue: "queue.Queue",
        status: dict,
        show_cursor: bool = True,
        generation: int = 0,
        is_current=None,
    ) -> None:
        super().__init__(daemon=True)
        self.source = dict(source)
        self.fps = max(5, min(60, int(fps)))
        self.scale = scale
        self.ndi_name = ndi_name
        self.sender = sender
        self.preview_queue = preview_queue
        self.status = status
        self.show_cursor = bool(show_cursor)
        # Generation guard: the app bumps its generation on every stop/start,
        # so a stale thread that outlives its session never clobbers the new
        # session's shared status (e.g. clearing running right after a restart).
        self.generation = generation
        self.is_current = is_current  # Callable[[int], bool] | None
        self._stop = threading.Event()

    def _current(self) -> bool:
        """True when this thread still owns the shared status dict."""
        try:
            return True if self.is_current is None else bool(self.is_current(self.generation))
        except Exception:
            return True

    def stop(self) -> None:
        self._stop.set()

    # -- internals -----------------------------------------------------
    def _clamp_region(self, sct, region: dict) -> dict:
        """Intersect the requested rect with the visible desktop.

        Window geometries from xwininfo often extend past the screen edge
        (shadows/decorations); grabbing out-of-bounds raises X11 errors,
        so clamp to the union of all monitors.
        """
        try:
            mons = sct.monitors[1:]
            if not mons:
                return region
            dx0 = min(m["left"] for m in mons)
            dy0 = min(m["top"] for m in mons)
            dx1 = max(m["left"] + m["width"] for m in mons)
            dy1 = max(m["top"] + m["height"] for m in mons)
            x0 = max(region["left"], dx0)
            y0 = max(region["top"], dy0)
            x1 = min(region["left"] + region["width"], dx1)
            y1 = min(region["top"] + region["height"], dy1)
            if x1 - x0 >= 64 and y1 - y0 >= 64:
                return {"left": x0, "top": y0,
                        "width": x1 - x0, "height": y1 - y0}
        except Exception:
            pass
        return region

    def _grab_region(self, sct, region: dict):
        try:
            return sct.grab(self._clamp_region(sct, region))
        except Exception as e:
            self.status["last_error"] = f"grab failed: {e}"
            return None

    def run(self) -> None:
        period = 1.0 / self.fps
        src = self.source
        kind = src.get("kind", "window")

        if kind == "test":
            out_w, out_h = compute_output_size(1280, 720, self.scale)
        else:
            out_w, out_h = compute_output_size(
                max(2, int(src.get("w", 1280))), max(2, int(src.get("h", 720))),
                self.scale,
            )

        try:
            if not self._current():
                # Superseded before the thread got going (e.g. instant STOP
                # after GO LIVE) — don't touch the shared sender/status.
                return
            self.sender.open(self.ndi_name, out_w, out_h, self.fps)
        except Exception as e:
            self.status["last_error"] = f"NDI open failed: {e}"
            if self._current():
                self.status["running"] = False
            return

        self.status.update({
            "running": True, "out_w": out_w, "out_h": out_h,
            "frames": 0, "actual_fps": 0.0, "last_error": "",
        })

        sct = None
        if kind != "test":
            try:
                import mss
                sct = mss.mss()
            except Exception as e:
                self.status["last_error"] = (
                    f"screen capture unavailable: {e}. "
                    "Use Test Pattern on Wayland without XWayland."
                )
                self.status["running"] = False
                return

        frame_count = 0
        ema_fps = float(self.fps)
        t0 = time.monotonic()
        next_t = t0

        try:
            while not self._stop.is_set() and self._current():
                now = time.monotonic()
                if now < next_t:
                    time.sleep(min(0.005, next_t - now))
                    continue
                # Keep pace; if we fell behind >0.5s, resync clock.
                if now - next_t > 0.5:
                    next_t = now
                next_t += period

                if kind == "test":
                    img = make_test_pattern(out_w, out_h, now - t0)
                    if img is None:
                        self.status["last_error"] = "Pillow+numpy required"
                        break
                else:
                    region = {
                        "left": int(src.get("x", 0)),
                        "top": int(src.get("y", 0)),
                        "width": max(2, int(src.get("w", 640))),
                        "height": max(2, int(src.get("h", 480))),
                    }
                    assert sct is not None
                    shot = self._grab_region(sct, region)
                    if shot is None:
                        time.sleep(0.05)
                        continue
                    if not HAVE_PIL:
                        self.status["last_error"] = "Pillow required"
                        break
                    img = Image.frombytes(
                        "RGB", shot.size, shot.bgra, "raw", "BGRX")
                    if (img.width, img.height) != (out_w, out_h):
                        img = img.resize((out_w, out_h), Image.BILINEAR)

                    # Cursor overlay (mss never captures the pointer).
                    if self.show_cursor:
                        try:
                            cpos = get_cursor_pos()
                            if cpos is not None:
                                cx, cy = cpos
                                rx = cx - region["left"]
                                ry = cy - region["top"]
                                rw, rh = region["width"], region["height"]
                                if 0 <= rx < rw and 0 <= ry < rh and rw > 0 and rh > 0:
                                    ox = rx * out_w / rw
                                    oy = ry * out_h / rh
                                    if 0 <= ox < out_w and 0 <= oy < out_h:
                                        draw_cursor_overlay(img, ox, oy)
                        except Exception:
                            pass

                # Preview (latest-frame only)
                try:
                    thumb = img.copy()
                    thumb.thumbnail((640, 360), Image.BILINEAR)
                    while True:
                        try:
                            self.preview_queue.get_nowait()
                        except queue.Empty:
                            break
                    self.preview_queue.put_nowait(thumb)
                except Exception:
                    pass

                # NDI send as BGRA
                try:
                    if HAVE_NUMPY:
                        import numpy as _np
                        arr = _np.asarray(img)  # HxWx3 RGB
                        h, w = arr.shape[:2]
                        bgra = _np.empty((h, w, 4), dtype=_np.uint8)
                        bgra[:, :, 0] = arr[:, :, 2]
                        bgra[:, :, 1] = arr[:, :, 1]
                        bgra[:, :, 2] = arr[:, :, 0]
                        bgra[:, :, 3] = 255
                        # pass the writable array itself (zero-copy)
                        self.sender.send(bgra)
                    else:
                        rgba = img.convert("RGBA")
                        r, g, b, a = rgba.split()
                        from PIL import Image as _I
                        bgra_img = _I.merge("RGBA", (b, g, r, a))
                        self.sender.send(bgra_img.tobytes())
                except Exception as e:
                    self.status["last_error"] = f"send failed: {e}"

                frame_count += 1
                elapsed = time.monotonic() - t0
                if elapsed > 0 and frame_count % 5 == 0:
                    inst = frame_count / elapsed
                    ema_fps = 0.85 * ema_fps + 0.15 * inst
                    if self._current():
                        self.status["actual_fps"] = round(ema_fps, 1)
                        self.status["frames"] = frame_count
        finally:
            if sct is not None:
                try:
                    sct.close()
                except Exception:
                    pass
            if self._current():
                self.status["running"] = False
                self.status["frames"] = frame_count
