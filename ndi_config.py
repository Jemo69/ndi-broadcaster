"""NDI discovery-server config helper.

Same-subnet receivers find our stream automatically via mDNS. To be visible
across the FULL network (other subnets/VLANs), the sender must register with
an NDI Discovery Server:

- Linux: the NDI SDK reads ~/.ndi/ndi-config.v1.json -> networks.discovery.
  This module manages that file (merge, never clobber unrelated keys).
- Windows/macOS: configured in NDI Access Manager -> Advanced tab
  (this module reports unsupported there and the UI shows guidance).

NOTE: when a discovery server is set, the sender STOPS using mDNS, so it
becomes invisible to receivers that only use mDNS. Sender and receivers must
point at the same server (or keep it empty for plain same-subnet mDNS).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def supported() -> bool:
    """True where this module can write the discovery config itself."""
    return sys.platform.startswith("linux")


def config_path() -> Path:
    return Path.home() / ".ndi" / "ndi-config.v1.json"


def get_discovery_server() -> str:
    """Return the configured discovery server(s), or '' for mDNS-only."""
    try:
        data = json.loads(config_path().read_text())
        return str(data.get("networks", {}).get("discovery", "") or "")
    except Exception:
        return ""


def set_discovery_server(ips: str) -> Path:
    """Set (or clear with '') the discovery server list. Merges with existing file."""
    if not supported():
        raise OSError("Discovery config is managed via NDI Access Manager on this OS.")
    path = config_path()
    data: dict = {}
    try:
        if path.exists():
            data = json.loads(path.read_text() or "{}")
            if not isinstance(data, dict):
                data = {}
    except Exception:
        data = {}
    nets = data.get("networks")
    if not isinstance(nets, dict):
        nets = {}
        data["networks"] = nets
    cleaned = ",".join(p.strip() for p in ips.split(",") if p.strip())
    nets["discovery"] = cleaned
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))
    return path
