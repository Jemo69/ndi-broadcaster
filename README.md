# NDI Broadcaster (Python + Tk)

Pick any app window (or a monitor, or a custom region) and broadcast it as an
**NDI** video source on your LAN. Find it in NDI Studio Monitor, OBS (NDI plugin),
vMix, Resolume, etc.

![stack](https://img.shields.io/badge/python-3.10%2B-blue) ![ui](https://img.shields.io/badge/ui-tkinter-dark) ![ndi](https://img.shields.io/badge/ndi-cyndilib-cyan)

## Features

- 🪟 **Window picker** — lists X11 / XWayland windows (title, app, geometry) with search
- ◫ **Monitor capture** + ▦ **drag-to-select custom region**
- ≋ **Test pattern** source (checks your NDI chain without screen permissions)
- 👀 Live preview in-app, actual-FPS + resolution stats, viewer count
- 🎚 Frame rate (15–60) and output scale (Original / 1080p / 720p / 540p / 360p)
- 🌙 Polished dark Tk UI

## Download (Windows .exe)

No Python needed: grab `NDI-Broadcaster.exe` from the
[**Releases**](https://github.com/Jemo69/ndi-broadcaster/releases) page
(built automatically by GitHub Actions on every `v*` tag) and double-click it.

> First launch may take ~10s (one-file bundle unpacking) and Windows
> SmartScreen may warn about the unknown publisher — click *More info → Run anyway*.

## Quick start (from source)

```bash
pip install -r requirements.txt
python3 app.py
# or
sh run.sh
```

1. Pick a source on the left (or **Pick region…** and drag a rectangle).
2. Set the NDI name, FPS and scale.
3. Press **▶ Preview** to check the picture, then **● GO LIVE**.

## How it works

- Capture: [`mss`](https://github.com/BoboTiG/python-mss) grabs the window's screen
  rectangle each frame; Pillow resizes; frames are converted to **BGRA**.
- NDI: [`cyndilib`](https://github.com/cyndilib/cyndilib) sends BGRA frames with
  `write_video_async`. If `cyndilib` can't be imported, the app still runs in
  **preview-only mode** so you can test the UI/capture.
- Capture runs in a background thread; Tk pulls the latest preview frame via a
  size-1 queue, so the UI never blocks.

## Network visibility — same subnet vs full network

- **Default (empty Discovery Server):** the stream advertises via mDNS, so it is
  found automatically by receivers **on the same subnet**. The sending PC and the
  viewing PC must be on the same LAN/subnet.
- **Full network (other subnets/VLANs):** run
  [NDI Discovery Server](https://docs.ndi.video/all/using-ndi/utilities/discovery-service)
  (free, in NDI Tools) on a machine reachable from all subnets, then:
  - on **Linux** enter its IP in the app's **Discovery Server** field (the app
    writes `~/.ndi/ndi-config.v1.json` for you before going live);
  - on **Windows/macOS** enter it in **NDI Access Manager → Advanced** on this PC.
  - ⚠️ Trade-off (NDI design): with a server set, mDNS is **off** — receivers must
    point at the **same** server or they won't see the stream.
- **Windows Firewall:** on first launch allow `NDI-Broadcaster` on **Private**
  networks, otherwise other PCs can't see or pull the stream.
- Changing Discovery Server applies on the next GO LIVE (sender re-registers).

## Windows notes

- The window list, monitor capture and region picker all work on Windows
  (enumeration via Win32 `EnumWindows`, no extra install). Keep the source window
  visible — capture is region-based, overlaps get captured too.
- If a window is missing from the list, it is likely a minimized or
  title-less helper window; restore it and press ⟳.

## Notes / limitations

- **Wayland:** due to Wayland isolation, only **XWayland** windows are listable
  (same limit as `xdotool`/`xwininfo`). For native Wayland apps, capture the full
  monitor or use **Pick region**. For true per-window isolation on Wayland you
  would need an `xdg-desktop-portal` screencast integration — not included in v1.
- Window capture is **region-based**: if another window covers it, the overlap is
  captured too. Keep the source window visible and on top for a clean feed.
- Changing FPS/scale/source while live restarts the sender (viewers re-acquire
  in ~1s).
- Requires `xwininfo` (package `x11-utils`) for the richest window list;
  falls back to `xdotool` otherwise.

## Files

| file | what |
|---|---|
| `app.py` | Tk UI (dark theme, preview, region picker) |
| `capture.py` | background capture thread + test pattern |
| `ndi_sender.py` | `cyndilib` wrapper with preview-only fallback |
| `ndi_config.py` | Discovery Server config (`~/.ndi/ndi-config.v1.json`) |
| `windows_util.py` | window/monitor enumeration |
| `requirements.txt` | `cyndilib`, `mss`, `Pillow`, `numpy` |
