"""Background capture engine: grabs a screen region (or test pattern)
at a target FPS, feeds preview frames to the UI and BGRA frames to NDI.
"""

from __future__ import annotations

import queue
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
    ) -> None:
        super().__init__(daemon=True)
        self.source = dict(source)
        self.fps = max(5, min(60, int(fps)))
        self.scale = scale
        self.ndi_name = ndi_name
        self.sender = sender
        self.preview_queue = preview_queue
        self.status = status
        self._stop = threading.Event()

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
            self.sender.open(self.ndi_name, out_w, out_h, self.fps)
        except Exception as e:
            self.status["last_error"] = f"NDI open failed: {e}"
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
            while not self._stop.is_set():
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
                    self.status["actual_fps"] = round(ema_fps, 1)
                    self.status["frames"] = frame_count
        finally:
            if sct is not None:
                try:
                    sct.close()
                except Exception:
                    pass
            self.status["running"] = False
            self.status["frames"] = frame_count
