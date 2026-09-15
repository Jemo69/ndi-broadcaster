#!/usr/bin/env python3
"""NDI Broadcaster — pick a window, broadcast it as NDI.

Run:
    pip install -r requirements.txt
    python3 app.py
"""

from __future__ import annotations

import queue
import sys
import threading
import tkinter as tk
from tkinter import ttk

import diag
import ndi_config
from capture import CaptureEngine
from ndi_sender import NdiSender, HAVE_NDI, CYNDILIB_VERSION
from windows_util import list_monitors, list_windows

try:
    from PIL import ImageTk
    HAVE_IMAGETK = True
except ImportError:
    HAVE_IMAGETK = False

# -- theme ---------------------------------------------------------------
BG_ROOT = "#0e1116"
BG_PANEL = "#141922"
BG_CARD = "#1b2230"
BG_INPUT = "#0b0e13"
BORDER = "#2a3444"
TEXT = "#e9eef5"
MUTED = "#8b96a8"
ACCENT = "#00c8ff"
ACCENT_DARK = "#0a9cc4"
GREEN = "#2ecc71"
RED = "#ff4d5e"
YELLOW = "#f5b942"

FONT_TITLE = ("Segoe UI", 15, "bold")
FONT_SECTION = ("Segoe UI", 9, "bold")
FONT_BODY = ("Segoe UI", 10)
FONT_SMALL = ("Segoe UI", 9)
FONT_MONO = ("Consolas", 9)


class RegionPicker(tk.Toplevel):
    """Let the user drag a rectangle over a screenshot to pick a region."""

    def __init__(self, master, on_pick):
        super().__init__(master)
        self.on_pick = on_pick
        self.title("Drag to select a region — Esc to cancel")
        self.configure(bg="black")
        self.attributes("-fullscreen", True)
        self.bind("<Escape>", lambda _e: self.destroy())

        try:
            import mss
            from PIL import Image, ImageTk as _ITk
        except ImportError:
            tk.messagebox.showerror("Missing dep", "mss + Pillow required.")  # type: ignore
            self.destroy()
            return

        with mss.mss() as sct:
            mon = sct.monitors[1]
            shot = sct.grab(mon)
            img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        sw, sh = shot.width, shot.height
        fw, fh = self.winfo_screenwidth(), self.winfo_screenheight()
        sx, sy = fw / max(1, sw), fh / max(1, sh)
        disp = img.resize((fw, fh))
        self._photo = _ITk.PhotoImage(disp)
        self._ox, self._oy = mon["left"], mon["top"]
        self._sx, self._sy = sw / max(1, fw), sh / max(1, fh)

        self.canvas = tk.Canvas(self, highlightthickness=0, bg="black",
                                cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.create_image(0, 0, image=self._photo, anchor="nw")
        self.canvas.create_text(fw // 2, 28, text="DRAG TO SELECT REGION  •  ESC TO CANCEL",
                                fill="white", font=("Segoe UI", 12, "bold"))
        self._start = None
        self._rect = None
        self.canvas.bind("<ButtonPress-1>", self._down)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._up)

    def _down(self, e):
        self._start = (e.x, e.y)
        if self._rect:
            self.canvas.delete(self._rect)
        self._rect = self.canvas.create_rectangle(e.x, e.y, e.x, e.y,
                                                  outline=ACCENT, width=2)

    def _drag(self, e):
        if self._start and self._rect:
            self.canvas.coords(self._rect, self._start[0], self._start[1], e.x, e.y)

    def _up(self, e):
        if not self._start:
            self.destroy()
            return
        x0, y0 = self._start
        x1, y1 = e.x, e.y
        x, y = int(min(x0, x1) * self._sx) + self._ox, int(min(y0, y1) * self._sy) + self._oy
        w, h = int(abs(x1 - x0) * self._sx), int(abs(y1 - y0) * self._sy)
        self.destroy()
        if w >= 64 and h >= 64:
            self.on_pick({"kind": "region", "label": f"Custom region {w}x{h}",
                           "x": x, "y": y, "w": w, "h": h})


class NdiBroadcasterApp(tk.Tk):
    def __init__(self):
        if sys.platform == "win32":
            # System-DPI awareness so GetWindowRect coords match mss pixels.
            try:
                import ctypes
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass
        super().__init__()
        self.title("NDI Broadcaster")
        self.geometry("1180x720")
        self.minsize(1020, 620)
        self.configure(bg=BG_ROOT)

        self.sources: list[dict] = []     # display list (windows+monitors+special)
        self.selected: dict | None = None
        self.engine: CaptureEngine | None = None
        self.sender = NdiSender()
        self.preview_q: queue.Queue = queue.Queue(maxsize=2)
        self.status: dict = {"running": False, "frames": 0, "actual_fps": 0.0,
                             "out_w": 0, "out_h": 0, "last_error": ""}
        self.previewing = False
        self.live = False
        self._gen = 0  # engine generation: bumped on every stop/start
        self._photo = None
        self.show_cursor = tk.BooleanVar(value=True)

        self._build_style()
        self._build_layout()
        self.refresh_sources()
        self.after(80, self._preview_tick)
        self.after(500, self._stats_tick)

    # -- styling ---------------------------------------------------------
    def _build_style(self):
        s = ttk.Style(self)
        try:
            s.theme_use("clam")
        except tk.TclError:
            pass
        s.configure(".", background=BG_PANEL, foreground=TEXT, font=FONT_BODY,
                    fieldbackground=BG_INPUT, bordercolor=BORDER)
        s.configure("TFrame", background=BG_PANEL)
        s.configure("Card.TFrame", background=BG_CARD)
        s.configure("TLabel", background=BG_PANEL, foreground=TEXT)
        s.configure("Card.TLabel", background=BG_CARD, foreground=TEXT)
        s.configure("Muted.TLabel", background=BG_PANEL, foreground=MUTED, font=FONT_SMALL)
        s.configure("CardMuted.TLabel", background=BG_CARD, foreground=MUTED, font=FONT_SMALL)
        s.configure("Section.TLabel", background=BG_PANEL, foreground=MUTED, font=FONT_SECTION)
        s.configure("TEntry", fieldbackground=BG_INPUT, foreground=TEXT,
                    bordercolor=BORDER, insertcolor=TEXT)
        s.configure("TCombobox", fieldbackground=BG_INPUT, foreground=TEXT,
                    background=BG_CARD, bordercolor=BORDER, arrowcolor=MUTED)
        s.map("TCombobox", fieldbackground=[("readonly", BG_INPUT)],
              background=[("readonly", BG_CARD)])
        s.configure("TButton", background="#232c3b", foreground=TEXT,
                    bordercolor=BORDER, font=FONT_BODY, padding=(10, 6))
        s.map("TButton", background=[("active", "#2e3a4f"), ("pressed", "#222b3c")])
        s.configure("Accent.TButton", background=ACCENT_DARK, foreground="white",
                    font=("Segoe UI", 11, "bold"), padding=(10, 10))
        s.map("Accent.TButton", background=[("active", ACCENT)])
        s.configure("Stop.TButton", background="#a02c38", foreground="white",
                    font=("Segoe UI", 11, "bold"), padding=(10, 10))
        s.map("Stop.TButton", background=[("active", RED)])
        s.configure("TCheckbutton", background=BG_PANEL, foreground=TEXT)

    # -- layout ----------------------------------------------------------
    def _panel(self, parent, **kw):
        f = ttk.Frame(parent, style="Card.TFrame", padding=14, **kw)
        f.configure(borderwidth=1, relief="solid")
        return f

    def _build_layout(self):
        # Header
        header = tk.Frame(self, bg=BG_ROOT, height=56)
        header.pack(fill="x", padx=16, pady=(12, 8))
        header.pack_propagate(False)
        tk.Label(header, text="◉  NDI Broadcaster", bg=BG_ROOT, fg=TEXT,
                 font=FONT_TITLE).pack(side="left")
        tk.Label(header, text="window → NDI output", bg=BG_ROOT, fg=MUTED,
                 font=FONT_SMALL).pack(side="left", padx=(10, 0), pady=(6, 0))
        self.live_pill = tk.Label(header, text="●  IDLE", bg="#1d2532", fg=MUTED,
                                  font=("Segoe UI", 10, "bold"), padx=14, pady=6)
        self.live_pill.pack(side="right")
        backend = f"cyndilib {CYNDILIB_VERSION}" if HAVE_NDI else "preview-only (no NDI lib)"
        tk.Label(header, text=backend, bg=BG_ROOT, fg=MUTED,
                 font=FONT_SMALL).pack(side="right", padx=(0, 12), pady=(6, 0))

        main = tk.Frame(self, bg=BG_ROOT)
        main.pack(fill="both", expand=True, padx=16, pady=(0, 8))
        main.columnconfigure(0, weight=0, minsize=330)
        main.columnconfigure(1, weight=1)
        main.columnconfigure(2, weight=0, minsize=290)
        main.rowconfigure(0, weight=1)

        # ---- left: sources ----
        left = self._panel(main)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        ttk.Label(left, text="SOURCE", style="Section.TLabel").pack(anchor="w")  # type: ignore
        search_row = ttk.Frame(left, style="Card.TFrame")
        search_row.pack(fill="x", pady=(6, 6))
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._render_list())
        search = ttk.Entry(search_row, textvariable=self.search_var)
        search.pack(side="left", fill="x", expand=True)
        ttk.Button(search_row, text="⟳", width=3,
                   command=self.refresh_sources).pack(side="right", padx=(6, 0))

        self.src_list = tk.Listbox(left, bg=BG_INPUT, fg=TEXT,
                                   selectbackground=ACCENT_DARK,
                                   selectforeground="white", font=FONT_BODY,
                                   highlightthickness=1, highlightcolor=BORDER,
                                   activestyle="none", height=18)
        self.src_list.pack(fill="both", expand=True)
        self.src_list.bind("<<ListboxSelect>>", self._on_select)
        self.src_detail = ttk.Label(left, text="No source selected",
                                    style="CardMuted.TLabel", wraplength=300,
                                    justify="left")
        self.src_detail.pack(anchor="w", pady=(8, 0))
        btn_row = ttk.Frame(left, style="Card.TFrame")
        btn_row.pack(fill="x", pady=(8, 0))
        ttk.Button(btn_row, text="▦ Pick region…",
                   command=self._open_region_picker).pack(side="left", fill="x", expand=True)
        ttk.Button(btn_row, text="▶ Preview",
                   command=self.toggle_preview).pack(side="right", padx=(6, 0))

        # ---- center: preview ----
        center = self._panel(main)
        center.grid(row=0, column=1, sticky="nsew", padx=8)
        top = ttk.Frame(center, style="Card.TFrame")
        top.pack(fill="x")
        ttk.Label(top, text="PREVIEW", style="Section.TLabel").pack(side="left")  # type: ignore
        self.stat_label = ttk.Label(top, text="—", style="CardMuted.TLabel")
        self.stat_label.pack(side="right")
        self.preview_label = tk.Label(center, bg="black", fg=MUTED,
                                      text="Select a source, then press  ▶ Preview  or  GO LIVE",
                                      font=("Segoe UI", 11), compound="center")
        self.preview_label.pack(fill="both", expand=True, pady=(10, 4))
        self.hint_label = ttk.Label(
            center, style="CardMuted.TLabel",
            text="Wayland: only XWayland windows list — else Pick region / monitor. • "
                 "Stream not found elsewhere? Same subnet + firewall allow; cross-subnet "
                 "needs a Discovery Server.")
        self.hint_label.pack(anchor="w")

        # ---- right: output ----
        right = self._panel(main)
        right.grid(row=0, column=2, sticky="nsew", padx=(8, 0))
        ttk.Label(right, text="OUTPUT", style="Section.TLabel").pack(anchor="w")  # type: ignore

        ttk.Label(right, text="NDI source name", style="CardMuted.TLabel").pack(
            anchor="w", pady=(10, 2))
        self.ndi_name = tk.StringVar(value="TK Broadcaster (Window)")
        ttk.Entry(right, textvariable=self.ndi_name).pack(fill="x")

        row = ttk.Frame(right, style="Card.TFrame")
        row.pack(fill="x", pady=(10, 0))
        lf = ttk.Frame(row, style="Card.TFrame")
        lf.pack(side="left", fill="x", expand=True)
        ttk.Label(lf, text="Frame rate", style="CardMuted.TLabel").pack(anchor="w")
        self.fps_var = tk.StringVar(value="30")
        ttk.Combobox(lf, textvariable=self.fps_var, values=["15", "24", "25", "30", "50", "60"],
                     state="readonly", width=8).pack(anchor="w", pady=(2, 0))
        rf = ttk.Frame(row, style="Card.TFrame")
        rf.pack(side="right", fill="x", expand=True)
        ttk.Label(rf, text="Scale", style="CardMuted.TLabel").pack(anchor="w")
        self.scale_var = tk.StringVar(value="720p")
        ttk.Combobox(rf, textvariable=self.scale_var,
                     values=["Original", "1080p", "720p", "540p", "360p"],
                     state="readonly", width=10).pack(anchor="w", pady=(2, 0))

        self.preview_while_live = tk.BooleanVar(value=True)
        ttk.Checkbutton(right, text="Show preview while live",
                        variable=self.preview_while_live).pack(anchor="w", pady=(10, 0))

        ttk.Checkbutton(right, text="Show mouse cursor in broadcast",
                        variable=self.show_cursor,
                        command=self._on_cursor_toggle).pack(anchor="w", pady=(4, 0))

        ttk.Label(right, text="Discovery Server (cross-subnet, optional)",
                  style="CardMuted.TLabel").pack(anchor="w", pady=(10, 2))
        self.discovery_var = tk.StringVar(value=ndi_config.get_discovery_server())
        self.discovery_entry = ttk.Entry(right, textvariable=self.discovery_var)
        self.discovery_entry.pack(fill="x")
        if ndi_config.supported():
            ttk.Label(right, style="CardMuted.TLabel", wraplength=250, justify="left",
                      text="Empty = same-subnet mDNS (default). Set a server IP to be "
                           "visible network-wide — receivers must use the same server.").pack(anchor="w")
        else:
            self.discovery_entry.configure(state="disabled")
            ttk.Label(right, style="CardMuted.TLabel", wraplength=250, justify="left",
                      text="For cross-subnet visibility set the server in "
                           "NDI Access Manager → Advanced on this PC.").pack(anchor="w")

        self.go_btn = ttk.Button(right, text="●  GO LIVE", style="Accent.TButton",
                                 command=self.toggle_live)
        self.go_btn.pack(fill="x", pady=(14, 6))

        self.conn_label = ttk.Label(right, text="Viewers: —", style="CardMuted.TLabel")
        self.conn_label.pack(anchor="w")
        ttk.Button(right, text="🩺 Diagnose “can't see my stream”…",
                   command=self._open_diagnostics).pack(fill="x", pady=(6, 0))
        self.err_label = ttk.Label(right, text="", style="CardMuted.TLabel",
                                   wraplength=250, justify="left", foreground=YELLOW)
        self.err_label.pack(anchor="w", pady=(4, 0))

        ttk.Label(right, text="HOW IT WORKS", style="Section.TLabel").pack(
            anchor="w", pady=(14, 4))
        for line in ("1. Pick a window or monitor",
                     "2. Set NDI name + quality",
                     "3. GO LIVE — find it in NDI Studio Monitor / OBS"):
            ttk.Label(right, text=line, style="CardMuted.TLabel").pack(anchor="w")

        # Footer
        self.footer = tk.Label(self, text="Ready.", bg=BG_ROOT, fg=MUTED,
                               font=FONT_SMALL, anchor="w")
        self.footer.pack(fill="x", padx=18, pady=(0, 10))

    # -- sources ---------------------------------------------------------
    def refresh_sources(self):
        wins = []
        try:
            wins = list_windows()
        except Exception:
            wins = []
        mons = []
        try:
            mons = list_monitors()
        except Exception:
            mons = []
        items: list[dict] = []
        for w in wins:
            items.append({"kind": "window",
                          "label": f"▣ {w['title']}  ·  {w['w']}x{w['h']}",
                          "sub": f"{w.get('app','')}  {w['x']},{w['y']}  {w['w']}x{w['h']}",
                          **{k: w[k] for k in ("x", "y", "w", "h")},
                          "title": w["title"]})
        for m in mons:
            items.append({"kind": "monitor", "label": f"◫ {m['label']}",
                          "sub": f"Monitor {m['index']}  {m['x']},{m['y']}  {m['w']}x{m['h']}",
                          "x": m["x"], "y": m["y"], "w": m["w"], "h": m["h"]})
        items.append({"kind": "test", "label": "≋ Test pattern (no capture needed)",
                      "sub": "Animated bars — great for checking NDI end-to-end",
                      "w": 1280, "h": 720})
        self.sources = items
        self._render_list()
        n_win = sum(1 for i in items if i["kind"] == "window")
        self.footer.configure(
            text=f"Found {n_win} window(s), {len(mons)} monitor(s). "
                 + ("" if HAVE_NDI else "NDI lib missing: showing preview only — `pip install cyndilib`."))
        # keep selection if possible
        if self.selected:
            for i, it in enumerate(self._filtered()):
                if it["label"] == self.selected.get("label"):
                    self.src_list.selection_set(i)
                    break

    def _filtered(self) -> list[dict]:
        q = self.search_var.get().strip().lower()
        if not q:
            return self.sources
        return [s for s in self.sources if q in s["label"].lower()]

    def _render_list(self):
        cur = self.src_list.curselection()
        self.src_list.delete(0, "end")
        for s in self._filtered():
            self.src_list.insert("end", s["label"])
        if cur and cur[0] < self.src_list.size():
            self.src_list.selection_set(cur[0])

    def _on_select(self, _e=None):
        sel = self.src_list.curselection()
        if not sel:
            return
        items = self._filtered()
        if sel[0] >= len(items):
            return
        self.selected = items[sel[0]]
        s = self.selected
        if s["kind"] == "test":
            detail = "Test pattern · output follows Scale setting"
        else:
            detail = f"{s.get('title', s['label'])}\n{s.get('sub','')}"
        self.src_detail.configure(text=detail)
        if self.previewing or self.live:
            self._restart_engine()

    def _open_region_picker(self):
        RegionPicker(self, self._on_region_pick)

    def _open_diagnostics(self):
        DiagnosticsDialog(self, self)

    def _on_region_pick(self, region: dict):
        self.sources.append(region)
        self._render_list()
        idx = len(self._filtered()) - 1
        self.src_list.selection_clear(0, "end")
        self.src_list.selection_set(idx)
        self._on_select()

    # -- engine control --------------------------------------------------
    def _engine_params(self):
        fps = 30
        try:
            fps = int(self.fps_var.get())
        except ValueError:
            pass
        return fps, self.scale_var.get(), self.ndi_name.get().strip() or "TK Broadcaster"

    def _apply_discovery_config(self):
        """Write the discovery-server choice before the NDI sender opens."""
        if not ndi_config.supported():
            return
        want = self.discovery_var.get().strip()
        try:
            if want != ndi_config.get_discovery_server():
                ndi_config.set_discovery_server(want)
        except Exception as e:
            self.footer.configure(text=f"Discovery Server not saved: {e}")

    def _is_current_gen(self, gen: int) -> bool:
        """Generation check handed to the capture thread (see CaptureEngine)."""
        return gen == self._gen

    def _start_engine(self) -> bool:
        """(Re)start capture + sender for the current selection.

        Returns True when a capture thread was launched. Never leaves a
        stale thread behind: the previous engine is fully stopped first.
        """
        if not self.selected:
            self.footer.configure(text="Pick a source first.")
            return False
        self._stop_engine()
        self._gen += 1
        self._apply_discovery_config()
        fps, scale, name = self._engine_params()
        self.preview_q = queue.Queue(maxsize=2)
        self.engine = CaptureEngine(self.selected, fps, scale, name,
                                    self.sender, self.preview_q, self.status,
                                    show_cursor=self.show_cursor.get(),
                                    generation=self._gen,
                                    is_current=self._is_current_gen)
        self.engine.start()
        return True

    def _on_cursor_toggle(self):
        """Apply cursor choice instantly — no restart needed."""
        if self.engine is not None:
            try:
                self.engine.show_cursor = bool(self.show_cursor.get())
            except Exception:
                pass

    def _stop_engine(self) -> None:
        """Stop capture and close the sender. Never raises.

        The generation bump invalidates any stale capture thread first, so
        even if it takes a moment to exit it can no longer touch the shared
        status — a new engine can then start cleanly (STOP → GO LIVE).
        """
        eng, self.engine = self.engine, None
        self._gen += 1
        if eng is not None:
            try:
                eng.stop()
            except Exception:
                pass
            try:
                eng.join(timeout=5.0)
            except Exception:
                pass
            try:
                if eng.is_alive():
                    self.footer.configure(
                        text="Capture thread slow to stop — wait a moment, then GO LIVE.")
            except Exception:
                pass
        try:
            self.sender.close()
        except Exception:
            pass

    def _restart_engine(self):
        if self.live or self.previewing:
            self._start_engine()

    def toggle_preview(self):
        if self.live:
            return
        self.previewing = not self.previewing
        if self.previewing:
            if not self.selected:
                self.footer.configure(text="Pick a source first.")
                self.previewing = False
                return
            try:
                started = self._start_engine()
            except Exception as e:
                started = False
                self.footer.configure(text=f"Couldn't preview: {e}")
            if not started:
                self.previewing = False
                self.go_btn.configure(text="●  GO LIVE", style="Accent.TButton")
                self.live_pill.configure(text="●  IDLE", bg="#1d2532", fg=MUTED)
                return
            self.footer.configure(text="Previewing… (not broadcasting)")
        else:
            self._stop_engine()
            self._reset_idle_ui()
            self.footer.configure(text="Preview stopped.")

    def _reset_idle_ui(self):
        """Return visuals to the just-opened state (keeps source selection)."""
        self.status.update({"running": False, "frames": 0, "actual_fps": 0.0,
                            "out_w": 0, "out_h": 0, "last_error": ""})
        try:
            self.sender.reset_stats()
        except Exception:
            pass
        self.stat_label.configure(text="—")
        self.conn_label.configure(text="Viewers: —")
        self.err_label.configure(text="")
        self._photo = None
        self.preview_label.configure(
            image="", text="Select a source, then press  ▶ Preview  or  GO LIVE")

    def toggle_live(self):
        if not self.live:
            if not self.selected:
                self.footer.configure(text="Pick a source first.")
                return
            try:
                started = self._start_engine()
                fail_msg = ""
            except Exception as e:
                started = False
                fail_msg = f"Couldn't go live: {e}"
            if started and (self.engine is None):
                started = False
            if not started:
                # Stay (or return to) idle so ● GO LIVE is always available to retry.
                self.live = False
                self.go_btn.configure(text="●  GO LIVE", style="Accent.TButton")
                self.live_pill.configure(text="●  IDLE", bg="#1d2532", fg=MUTED)
                if not fail_msg:
                    fail_msg = (self.status.get("last_error")
                                or "Couldn't go live — press ● GO LIVE to retry.")
                    if not fail_msg.startswith("Couldn't"):
                        fail_msg = f"Couldn't go live: {fail_msg}"
                self.footer.configure(text=fail_msg)
                return
            self.live = True
            self.previewing = False
            self.go_btn.configure(text="■  STOP", style="Stop.TButton")
            self.live_pill.configure(text="●  LIVE", bg="#3a1420", fg=RED)
            mode = "LIVE on NDI" if HAVE_NDI else "LIVE (preview-only, no NDI lib)"
            self.footer.configure(text=f"{mode} as “{self._engine_params()[2]}”.")
        else:
            # STOP always lands back on a working ● GO LIVE button, even if
            # teardown itself hits an error.
            self.live = False
            try:
                self._stop_engine()
            finally:
                self._reset_idle_ui()
                self.go_btn.configure(text="●  GO LIVE", style="Accent.TButton")
                self.live_pill.configure(text="●  IDLE", bg="#1d2532", fg=MUTED)
                self.footer.configure(text="Stopped — pick a source and GO LIVE when ready.")

    # -- ticks -----------------------------------------------------------
    def _preview_tick(self):
        try:
            show = self.previewing or (self.live and self.preview_while_live.get())
            if show and HAVE_IMAGETK:
                try:
                    thumb = self.preview_q.get_nowait()
                    box_w = max(200, self.preview_label.winfo_width() - 4)
                    box_h = max(150, self.preview_label.winfo_height() - 4)
                    img = thumb.copy()
                    img.thumbnail((box_w, box_h))
                    self._photo = ImageTk.PhotoImage(img)
                    self.preview_label.configure(image=self._photo, text="")
                except queue.Empty:
                    pass
            elif not (self.previewing or self.live):
                if str(self.preview_label.cget("text")) == "":
                    self.preview_label.configure(
                        image="", text="Select a source, then press  ▶ Preview  or  GO LIVE")
        except Exception:
            pass
        self.after(80, self._preview_tick)

    def _stats_tick(self):
        try:
            if self.live and not self.status.get("running"):
                # Engine died (failed start or mid-broadcast fault) — drop back
                # to idle so ● GO LIVE is always available to retry.
                try:
                    alive = self.engine is not None and self.engine.is_alive()
                except Exception:
                    alive = False
                if not alive:
                    err = (self.status.get("last_error") or "capture stopped").strip()
                    self.live = False
                    self._reset_idle_ui()
                    self.go_btn.configure(text="●  GO LIVE", style="Accent.TButton")
                    self.live_pill.configure(text="●  IDLE", bg="#1d2532", fg=MUTED)
                    self.footer.configure(
                        text=f"Broadcast stopped ({err}) — press ● GO LIVE to retry.")
                    return
            if self.engine is not None and self.status.get("running"):
                fps = self.status.get("actual_fps", 0.0)
                n = self.status.get("frames", 0)
                w, h = self.status.get("out_w", 0), self.status.get("out_h", 0)
                self.stat_label.configure(text=f"{w}x{h}  ·  {fps} fps  ·  {n} frames")
                if self.live:
                    try:
                        c = self.sender.connections()
                        self.conn_label.configure(text=f"Viewers: {c}")
                    except Exception:
                        pass
            err = self.status.get("last_error", "")
            if err:
                self.err_label.configure(text=f"⚠ {err[:160]}")
        except Exception:
            pass
        self.after(500, self._stats_tick)

    def on_close(self):
        try:
            self._stop_engine()
        finally:
            self.destroy()


class DiagnosticsDialog(tk.Toplevel):
    """'Why can't I see my stream?' — versions, IPs, live network scan."""

    def __init__(self, master, app: "NdiBroadcasterApp"):
        super().__init__(master)
        self.app = app
        self.title("NDI Diagnostics")
        self.geometry("560x520")
        self.configure(bg=BG_CARD)
        ttk.Label(self, text="NDI DIAGNOSTICS", style="Section.TLabel").pack(  # type: ignore
            anchor="w", padx=14, pady=(12, 4))

        self.text = tk.Text(self, bg=BG_INPUT, fg=TEXT, font=FONT_MONO,
                            highlightthickness=1, highlightcolor=BORDER,
                            padx=10, pady=10, wrap="word", height=22)
        self.text.pack(fill="both", expand=True, padx=14)
        self.text.configure(state="disabled")

        row = ttk.Frame(self, style="Card.TFrame")
        row.pack(fill="x", padx=14, pady=10)
        self.scan_btn = ttk.Button(row, text="🔍 Scan network for NDI sources",
                                   command=self._run_scan)
        self.scan_btn.pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="⧉ Copy report",
                   command=self._copy).pack(side="right", padx=(8, 0))
        self._write_report(sources=None)

    def _report(self, sources) -> str:
        b = diag.backend_report()
        lines = [
            f"Backend: {'cyndilib ' + b['cyndilib'] if b['have_ndi'] else 'PREVIEW-ONLY (no NDI lib!)'}",
        ]
        if b.get("import_error"):
            lines.append(f"Import error: {b['import_error']}")
        lines += [
            f"NDI runtime: {b['runtime'] or '—'}",
            f"Sender: {'OPEN' if self.app.sender.is_open else 'closed'}"
            + (f"  name={self.app._engine_params()[2]}"
               f"  {self.app.sender.resolution[0]}x{self.app.sender.resolution[1]}"
               if self.app.sender.is_open else ""),
            f"Frames sent: {self.app.sender.frames_sent}",
            f"Send errors: {self.app.sender.last_send_error or 'none'}",
            f"Local IPs: {', '.join(diag.local_ips())}",
            "",
        ]
        if sources is None:
            lines.append("Network scan: not run yet — press Scan. (Go LIVE first,")
            lines.append("then check your stream name appears below.)")
        elif not sources:
            lines.append("Network scan: NO NDI sources visible at all — even mDNS")
            lines.append("discovery looks broken on this PC (firewall/VPN/network")
            lines.append("profile?). Fix that before anything else.")
        else:
            lines.append(f"Network scan: {len(sources)} source(s) visible:")
            lines.extend(f"  • {s}" for s in sources)
            own = self.app._engine_params()[2]
            lines.append("")
            lines.append("YOUR stream " + ("IS visible ✔" if any(own in s for s in sources)
                                           else "NOT visible ✘ (sender may be closed)"))
        lines += [
            "",
            "If YOUR stream shows above but not on the other PC:",
            " 1. Same subnet? (compare IPs — first 3 numbers must match)",
            " 2. Firewall allowed on Private network? (sender AND receiver)",
            " 3. No VPN active? (VPNs usually block mDNS discovery)",
            " 4. Receiver up to date? (NDI 6 stream, use current NDI Tools)",
            " 5. Other subnets? Set the same Discovery Server on both ends.",
        ]
        return "\n".join(lines)

    def _write_report(self, sources) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", self._report(sources))
        self.text.configure(state="disabled")

    def _run_scan(self):
        self.scan_btn.configure(state="disabled", text="Scanning… (~6s)")
        threading.Thread(target=self._scan_worker, daemon=True).start()

    def _scan_worker(self):
        names = diag.scan_sources(6.0)
        self.after(0, lambda: self._scan_done(names))

    def _scan_done(self, names):
        self._write_report(names)
        self.scan_btn.configure(state="normal", text="🔍 Scan network for NDI sources")

    def _copy(self):
        self.clipboard_clear()
        self.clipboard_append(self.text.get("1.0", "end").strip())
        self.update()


def main():
    app = NdiBroadcasterApp()
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()


if __name__ == "__main__":
    main()
