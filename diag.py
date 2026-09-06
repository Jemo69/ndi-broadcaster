"""Self-diagnostics for 'I don't see my stream' troubleshooting.

- backend_report(): NDI library state (the same check the header pill uses)
- local_ips(): all local IPv4 addresses (spots wrong-network/VPN issues)
- scan_sources(): Finder-based network scan listing every visible NDI source
"""

from __future__ import annotations

import socket


def backend_report() -> dict:
    from ndi_sender import HAVE_NDI, CYNDILIB_VERSION
    info: dict = {"have_ndi": HAVE_NDI, "cyndilib": CYNDILIB_VERSION,
                  "runtime": "", "import_error": ""}
    if HAVE_NDI:
        try:
            from cyndilib import ndi_version
            info["runtime"] = str(ndi_version)
        except Exception as e:
            info["runtime"] = f"unreadable ({e})"
    else:
        try:
            from ndi_sender import _IMPORT_ERROR
            info["import_error"] = str(_IMPORT_ERROR)[:500]
        except Exception:
            pass
    return info


def local_ips() -> list[str]:
    ips: set[str] = set()
    try:
        _, _, addrs = socket.gethostbyname_ex(socket.gethostname())
        ips.update(a for a in addrs if not a.startswith("127."))
    except Exception:
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            ips.add(s.getsockname()[0])
        finally:
            s.close()
    except Exception:
        pass
    return sorted(ips) or ["(none found)"]


def scan_sources(timeout_s: float = 6.0) -> list[str]:
    """Return sorted NDI source names visible from this machine."""
    from cyndilib import Finder
    finder = Finder()
    try:
        finder.open()
        try:
            finder.wait_for_sources(int(timeout_s * 1000))
        except Exception:
            pass
        try:
            return sorted(finder.get_source_names())
        except Exception:
            return []
    except Exception:
        return []
    finally:
        try:
            finder.close()
        except Exception:
            pass
