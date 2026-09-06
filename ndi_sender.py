"""NDI sender wrapper.

Uses `cyndilib` when available, otherwise degrades to a fake sender so
the app (preview, capture stats, UI) still works in "preview-only" mode.
"""

from __future__ import annotations

from fractions import Fraction

try:
    from cyndilib import Sender, VideoSendFrame
    from cyndilib.wrapper.ndi_structs import FourCC
    import cyndilib as _cyndilib_mod
    CYNDILIB_VERSION = getattr(_cyndilib_mod, "__version__", "installed")
    HAVE_NDI = True
except Exception as _e:  # pragma: no cover - environment dependent
    Sender = None  # type: ignore
    VideoSendFrame = None  # type: ignore
    FourCC = None  # type: ignore
    CYNDILIB_VERSION = ""
    HAVE_NDI = False
    _IMPORT_ERROR = str(_e)
else:
    _IMPORT_ERROR = ""


class NdiSender:
    """Thin wrapper around cyndilib.Sender sending BGRA frames."""

    def __init__(self) -> None:
        self.available = HAVE_NDI
        self._sender = None
        self._name = ""
        self._w = 0
        self._h = 0
        self._fps = 30
        self._opened = False
        self.frames_sent = 0

    @property
    def import_error(self) -> str:
        return _IMPORT_ERROR

    @property
    def is_open(self) -> bool:
        return self._opened

    @property
    def resolution(self) -> tuple[int, int]:
        return (self._w, self._h)

    def open(self, name: str, width: int, height: int, fps: int) -> None:
        if not self.available:
            # Fake open so the rest of the pipeline keeps working.
            self._name, self._w, self._h, self._fps = name, width, height, fps
            self._opened = True
            return
        self.close()
        assert Sender is not None and VideoSendFrame is not None
        sender = Sender(name)
        vf = VideoSendFrame()
        vf.set_resolution(int(width), int(height))
        try:
            vf.set_frame_rate(Fraction(int(fps), 1))
        except Exception:
            vf.set_frame_rate(Fraction(30, 1))
        vf.set_fourcc(FourCC.BGRA)
        sender.set_video_frame(vf)
        # Sender supports context-manager usage (`with sender:`) and an
        # explicit open(). Prefer open(), fall back to __enter__.
        opened = False
        if hasattr(sender, "open"):
            try:
                sender.open()
                opened = True
            except Exception:
                opened = False
        if not opened and hasattr(sender, "__enter__"):
            try:
                sender.__enter__()
                opened = True
            except Exception:
                opened = False
        self._sender = sender
        self._name, self._w, self._h, self._fps = name, width, height, fps
        self._opened = True

    def send(self, bgra: bytes | bytearray | memoryview) -> None:
        if not self._opened:
            return
        self.frames_sent += 1
        if not self.available or self._sender is None:
            return  # preview-only: just count
        try:
            mv = bgra if isinstance(bgra, memoryview) else memoryview(bgra)
            try:
                self._sender.write_video_async(mv)
            except AttributeError:
                # Older API: write into frame then send.
                vf = self._sender.video_frame
                vf.write_data(mv)
                try:
                    self._sender.send_video_async()
                except AttributeError:
                    self._sender.send_video()
        except Exception:
            pass  # never let NDI errors kill the capture loop

    def connections(self) -> int:
        if not self.available or self._sender is None or not self._opened:
            return 0
        try:
            return int(self._sender.get_num_connections(0))
        except Exception:
            return 0

    def close(self) -> None:
        if self._sender is not None:
            for meth in ("close", "__exit__"):
                try:
                    fn = getattr(self._sender, meth)
                    fn(None, None, None) if meth == "__exit__" else fn()
                    break
                except Exception:
                    continue
            self._sender = None
        self._opened = False
