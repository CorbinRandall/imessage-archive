#!/usr/bin/env python3
"""iMessage Archive — Terminal CLI (status + backup with live progress).

Usage:
  imessage-archive              # live status (default)
  imessage-archive status       # one-shot status
  imessage-archive backup       # run backup in foreground with progress
  imessage-archive setup        # install/repair agent + config
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HOME = Path.home()
CONFIG_FILE = Path(os.environ.get("CONFIG_FILE", HOME / ".config/imessage-archive.env"))
STATE_FILE = HOME / ".config/imessage-archive-agent.json"
INSTALL_DIR = Path(os.environ.get("INSTALL_DIR", HOME / ".local/imessage-archive"))


# ----- tiny terminal helpers -------------------------------------------------

def c(code: str, text: str) -> str:
    if not sys.stdout.isatty():
        return text
    return f"\033[{code}m{text}\033[0m"


def green(t: str) -> str:
    return c("32", t)


def yellow(t: str) -> str:
    return c("33", t)


def red(t: str) -> str:
    return c("31", t)


def dim(t: str) -> str:
    return c("2", t)


def bold(t: str) -> str:
    return c("1", t)


def bar(pct: float, width: int = 28) -> str:
    pct = max(0.0, min(100.0, pct))
    filled = int(round(width * pct / 100.0))
    return "[" + "#" * filled + "-" * (width - filled) + f"] {pct:5.1f}%"


def clear_line() -> None:
    if sys.stdout.isatty():
        sys.stdout.write("\r\033[K")


# ----- config / API ----------------------------------------------------------

def load_config() -> dict[str, str]:
    cfg: dict[str, str] = {}
    if CONFIG_FILE.exists():
        for line in CONFIG_FILE.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            cfg[k.strip()] = os.path.expandvars(v.strip().strip('"'))
    cfg.setdefault("SERVER_URL", os.environ.get("SERVER_URL", "http://192.168.1.200:8095").rstrip("/"))
    return cfg


def api(url: str, method: str = "GET", body: dict | None = None, token: str | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=12) as resp:
        raw = resp.read().decode()
        return json.loads(raw) if raw else {}


def ensure_registered(server: str) -> tuple[str, str, str]:
    state: dict = {}
    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text())
        except json.JSONDecodeError:
            state = {}
    if state.get("token") and state.get("server") == server:
        return state["client_id"], state["token"], state.get("name") or socket.gethostname()
    hostname = socket.gethostname()
    name = os.environ.get("CLIENT_NAME") or hostname
    reg = api(f"{server}/api/clients/register", "POST", {"name": name, "hostname": hostname})
    state = {"server": server, "client_id": reg["id"], "token": reg["token"], "name": reg["name"]}
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))
    return reg["id"], reg["token"], reg["name"]


def check_fda() -> tuple[bool, str]:
    exporter = shutil.which("imessage-exporter")
    if not exporter:
        return False, "imessage-exporter missing (brew install imessage-exporter)"
    try:
        r = subprocess.run([exporter, "-d"], capture_output=True, text=True, timeout=15)
        return (r.returncode == 0, "Full Disk Access OK" if r.returncode == 0 else "Full Disk Access needed")
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def agent_loaded() -> bool:
    try:
        r = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=5)
        return "imessage-archive.agent" in (r.stdout or "")
    except Exception:
        return False


def fmt_time(ts) -> str:
    if not ts:
        return "never"
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(float(ts)))
    except Exception:
        return str(ts)


def connection_state(server: str, client_id: str, token: str) -> dict:
    out: dict = {
        "label": "disconnected",
        "connected": False,
        "client": None,
        "run": None,
        "error": None,
    }
    try:
        api(f"{server}/health")
        out["connected"] = True
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)
        out["label"] = "disconnected"
        return out
    try:
        api(f"{server}/api/clients/heartbeat", "POST", {}, token=token)
        data = api(f"{server}/api/clients")
        for c in data.get("clients") or []:
            if c.get("id") == client_id:
                out["client"] = c
                break
        for r in data.get("runs") or []:
            if r.get("client_id") == client_id:
                out["run"] = r
                break
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)

    run = out.get("run") or {}
    if (run.get("status") or "").lower() == "running":
        out["label"] = "backing up"
    else:
        out["label"] = "connected"
    return out


def print_status_block(server: str, name: str, st: dict, fda_msg: str) -> None:
    label = st["label"]
    if label == "connected":
        head = green("● CONNECTED")
    elif label == "backing up":
        head = yellow("● BACKING UP")
    else:
        head = red("● DISCONNECTED")

    print(bold("iMessage Archive"))
    print(dim(server))
    print()
    print(f"  {head}  as {name}")
    print(f"  Permissions : {fda_msg}")
    print(f"  Agent       : {'running' if agent_loaded() else 'not loaded'}")
    client = st.get("client") or {}
    run = st.get("run") or {}
    print(f"  Last seen   : {fmt_time(client.get('last_seen_at'))}")
    print(f"  Last backup : {fmt_time(client.get('last_backup_at'))}"
          + (f" ({client.get('last_status')})" if client.get("last_status") else ""))
    if run:
        print(f"  Latest run  : {run.get('status')} / {run.get('phase') or '-'}")
        if run.get("message"):
            msg = str(run["message"]).replace("\n", " ")[:120]
            print(f"               {dim(msg)}")
    if st.get("error"):
        print(f"  Error       : {red(st['error'])}")
    print()


def cmd_status(watch: bool = False) -> int:
    cfg = load_config()
    server = cfg["SERVER_URL"].rstrip("/")
    try:
        cid, token, name = ensure_registered(server)
    except Exception as exc:  # noqa: BLE001
        print(red(f"Cannot reach {server}"))
        print(exc)
        return 1

    while True:
        fda_ok, fda_msg = check_fda()
        st = connection_state(server, cid, token)
        if watch and sys.stdout.isatty():
            sys.stdout.write("\033[2J\033[H")  # clear screen
        print_status_block(server, name, st, fda_msg)
        if not watch:
            return 0 if st["connected"] else 2
        print(dim("Refreshing every 3s — Ctrl+C to quit"))
        print(dim("Run backup with:  imessage-archive backup"))
        try:
            time.sleep(3)
        except KeyboardInterrupt:
            print()
            return 0


def report(server: str, token: str, run_id: str, status: str | None = None,
           phase: str | None = None, message: str | None = None) -> None:
    try:
        api(
            f"{server}/api/clients/backup/status",
            "POST",
            {"run_id": run_id, "status": status, "phase": phase, "message": message},
            token=token,
        )
    except Exception:
        pass


def cmd_backup() -> int:
    cfg = load_config()
    server = cfg["SERVER_URL"].rstrip("/")
    script = INSTALL_DIR / "client/export-and-sync.sh"
    if not script.exists():
        # fall back to sibling next to this file (bundled / repo checkout)
        alt = Path(__file__).resolve().parent / "export-and-sync.sh"
        script = alt if alt.exists() else script
    if not script.exists():
        print(red(f"Backup script not found at {INSTALL_DIR}/client/export-and-sync.sh"))
        print("Run: imessage-archive setup")
        return 1

    fda_ok, fda_msg = check_fda()
    if not fda_ok:
        print(red(fda_msg))
        print("System Settings → Privacy & Security → Full Disk Access")
        print("Enable: python3 and imessage-exporter")
        subprocess.run(
            ["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"],
            check=False,
        )
        return 1

    try:
        cid, token, name = ensure_registered(server)
    except Exception as exc:  # noqa: BLE001
        print(red(f"Cannot reach {server}: {exc}"))
        return 1

    print(bold("Starting backup"))
    print(dim(f"{name} → {server}"))
    print()

    start = api(
        f"{server}/api/clients/backup/start",
        "POST",
        {"triggered_by": "cli"},
        token=token,
    )
    run_id = start["run_id"]
    report(server, token, run_id, status="running", phase="export", message="CLI backup started")

    env = os.environ.copy()
    env["SERVER_URL"] = server
    env["CLIENT_TOKEN"] = token
    env["BACKUP_RUN_ID"] = run_id
    env["PATH"] = os.pathsep.join([
        "/opt/homebrew/bin",
        "/usr/local/bin",
        str(HOME / "bin"),
        env.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin"),
    ])
    env["CONFIG_FILE"] = str(CONFIG_FILE)
    # Force live progress in child tools
    env["IMESSAGE_ARCHIVE_PROGRESS"] = "1"

    print(yellow("Live progress below (export → Immich → sync → index)"))
    print(dim("─" * 56))
    # Stream output live — do NOT capture_output (that was why the GUI only showed "sync")
    proc = subprocess.run(["/bin/bash", str(script)], env=env)
    print(dim("─" * 56))
    if proc.returncode == 0:
        report(server, token, run_id, status="success", phase="done", message="Backup completed")
        print(green("✓ Backup complete"))
        print(dim(f"Dashboard: {server}"))
        return 0

    report(server, token, run_id, status="error", phase="failed",
           message=f"CLI backup exited {proc.returncode}")
    print(red(f"✗ Backup failed (exit {proc.returncode})"))
    return proc.returncode


def cmd_setup(server_url: str | None = None) -> int:
    """Install/repair local files + launchd agent."""
    cfg = load_config()
    server = (server_url or cfg.get("SERVER_URL") or "http://192.168.1.200:8095").rstrip("/")
    host = server.split("://", 1)[-1].split("/", 1)[0].split(":")[0]

    src = Path(__file__).resolve().parent
    INSTALL_DIR.mkdir(parents=True, exist_ok=True)
    (INSTALL_DIR / "client").mkdir(parents=True, exist_ok=True)
    for name in [
        "cli.py", "agent.py", "export-and-sync.sh", "export-contacts.py",
        "export-to-jsonl.py", "mount-share.sh", "upload-to-immich.py",
        "com.imessage-archive.agent.plist",
    ]:
        s = src / name
        if s.exists():
            shutil.copy2(s, INSTALL_DIR / "client" / name)
            if s.suffix in {".py", ".sh"}:
                os.chmod(INSTALL_DIR / "client" / name, 0o755)

    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not CONFIG_FILE.exists():
        CONFIG_FILE.write_text(
            f"SERVER_URL={server}\nSEARCH_API={server}\nUNRAID_HOST={host}\n"
            f"UNRAID_SHARE=Misc\nMOUNT_POINT=$HOME/mnt/unraid-imessage\n"
            f"LOCAL_EXPORT=$HOME/imessage-export\nCOPY_METHOD=full\n"
            f"IMMICH_URL=http://{host}:8090\nIMMICH_ALBUM=iMessage\n"
        )
    else:
        text = CONFIG_FILE.read_text()
        if "SERVER_URL=" in text:
            lines = []
            for line in text.splitlines():
                if line.startswith("SERVER_URL="):
                    lines.append(f"SERVER_URL={server}")
                elif line.startswith("SEARCH_API="):
                    lines.append(f"SEARCH_API={server}")
                else:
                    lines.append(line)
            CONFIG_FILE.write_text("\n".join(lines) + "\n")

    bin_dir = HOME / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    cli_path = INSTALL_DIR / "client" / "cli.py"
    link = bin_dir / "imessage-archive"
    if link.exists() or link.is_symlink():
        link.unlink()
    link.symlink_to(cli_path)
    # also keep imessage-backup
    backup_link = bin_dir / "imessage-backup"
    if backup_link.exists() or backup_link.is_symlink():
        backup_link.unlink()
    backup_link.symlink_to(INSTALL_DIR / "client" / "export-and-sync.sh")

    python = shutil.which("python3") or "/usr/bin/python3"
    plist_dst = HOME / "Library/LaunchAgents/com.imessage-archive.agent.plist"
    plist_src = INSTALL_DIR / "client/com.imessage-archive.agent.plist"
    body = plist_src.read_text().replace("HOME", str(HOME)).replace("PYTHON", python)
    plist_dst.write_text(body)
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/com.imessage-archive.agent"], capture_output=True)
    subprocess.run(["launchctl", "unload", str(plist_dst)], capture_output=True)
    r = subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist_dst)], capture_output=True)
    if r.returncode != 0:
        subprocess.run(["launchctl", "load", str(plist_dst)], capture_output=True)
    subprocess.run(["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/com.imessage-archive.agent"], capture_output=True)

    if not shutil.which("imessage-exporter"):
        print(yellow("Installing imessage-exporter via Homebrew…"))
        if not shutil.which("brew"):
            print(red("Homebrew required: https://brew.sh"))
            return 1
        subprocess.run(["brew", "install", "imessage-exporter"], check=False)

    try:
        ensure_registered(server)
    except Exception as exc:  # noqa: BLE001
        print(yellow(f"Registered later — server not reachable yet: {exc}"))

    print(green("✓ Setup complete"))
    print(f"  Config : {CONFIG_FILE}")
    print(f"  CLI    : {link}")
    print(f"  Server : {server}")
    if str(bin_dir) not in os.environ.get("PATH", ""):
        print(yellow("  Add to PATH:  export PATH=\"$HOME/bin:$PATH\""))
    print()
    print("Next:")
    print("  imessage-archive          # watch connection")
    print("  imessage-archive backup   # backup with progress")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="imessage-archive", description=__doc__)
    parser.add_argument(
        "command",
        nargs="?",
        default="status",
        choices=["status", "backup", "setup", "watch"],
        help="status (default), watch, backup, setup",
    )
    parser.add_argument("--server", default=None, help="Server URL (setup)")
    args = parser.parse_args(argv)

    if args.command in {"status"}:
        return cmd_status(watch=False)
    if args.command == "watch":
        return cmd_status(watch=True)
    if args.command == "backup":
        return cmd_backup()
    if args.command == "setup":
        return cmd_setup(args.server)
    return 0


if __name__ == "__main__":
    # Default with no args: live watch feels better for "open the app"
    if len(sys.argv) == 1:
        raise SystemExit(cmd_status(watch=True))
    raise SystemExit(main())
