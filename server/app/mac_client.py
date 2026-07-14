"""Build a downloadable macOS .app zip with SERVER_URL baked in."""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

# Shipped inside the Docker image next to this module (see mac_bundle/).
BUNDLE_DIR = Path(__file__).resolve().parent / "mac_bundle"
CLIENT_DIR = BUNDLE_DIR / "client"
CONFIG_DIR = BUNDLE_DIR / "config"

CLIENT_FILES = [
    "cli.py",
    "agent.py",
    "export-and-sync.sh",
    "export-contacts.py",
    "export-to-jsonl.py",
    "mount-share.sh",
    "upload-to-immich.py",
    "com.imessage-archive.agent.plist",
]


def build_mac_client_zip(server_url: str) -> bytes:
    if not BUNDLE_DIR.is_dir():
        raise FileNotFoundError(f"Mac client bundle missing at {BUNDLE_DIR}")

    server_url = server_url.rstrip("/")
    buf = io.BytesIO()
    app = "iMessage Archive.app"
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        plist = (BUNDLE_DIR / "Info.plist").read_bytes()
        info = zipfile.ZipInfo(f"{app}/Contents/Info.plist")
        info.external_attr = 0o644 << 16
        zf.writestr(info, plist)

        launcher = (BUNDLE_DIR / "launcher").read_bytes()
        info = zipfile.ZipInfo(f"{app}/Contents/MacOS/launcher")
        info.external_attr = 0o755 << 16
        zf.writestr(info, launcher)

        gui = (BUNDLE_DIR / "gui.py").read_bytes() if (BUNDLE_DIR / "gui.py").exists() else b""
        if gui:
            info = zipfile.ZipInfo(f"{app}/Contents/Resources/gui.py")
            info.external_attr = 0o755 << 16
            zf.writestr(info, gui)

        term = BUNDLE_DIR / "terminal-start.sh"
        if term.exists():
            info = zipfile.ZipInfo(f"{app}/Contents/Resources/terminal-start.sh")
            info.external_attr = 0o755 << 16
            zf.writestr(info, term.read_bytes())

        info = zipfile.ZipInfo(f"{app}/Contents/Resources/server.url")
        info.external_attr = 0o644 << 16
        zf.writestr(info, server_url.encode() + b"\n")

        for name in CLIENT_FILES:
            src = CLIENT_DIR / name
            if not src.exists():
                continue
            mode = 0o755 if src.suffix in {".py", ".sh"} else 0o644
            info = zipfile.ZipInfo(f"{app}/Contents/Resources/client/{name}")
            info.external_attr = mode << 16
            zf.writestr(info, src.read_bytes())

        env_example = CONFIG_DIR / "env.example"
        if env_example.exists():
            info = zipfile.ZipInfo(f"{app}/Contents/Resources/config/env.example")
            info.external_attr = 0o644 << 16
            zf.writestr(info, env_example.read_bytes())

        readme = f"""iMessage Archive — Mac client (Terminal)
========================================

1. Unzip and open "iMessage Archive.app"
   → Terminal opens with live status (CONNECTED / BACKING UP / DISCONNECTED)

2. In Terminal:
     imessage-archive backup
   Shows live progress (export → Immich upload bar → sync → done)

3. Grant Full Disk Access if prompted:
   System Settings → Privacy & Security → Full Disk Access
   Enable python3 and imessage-exporter

Server: {server_url}

Commands after install:
  imessage-archive          # live status
  imessage-archive status   # one-shot
  imessage-archive backup   # backup with progress
  imessage-archive setup    # repair install
"""
        info = zipfile.ZipInfo("README.txt")
        info.external_attr = 0o644 << 16
        zf.writestr(info, readme.encode())

    return buf.getvalue()
