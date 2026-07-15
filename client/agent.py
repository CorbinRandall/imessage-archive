#!/usr/bin/env python3
"""Mac agent: registers with server, polls schedule, runs backups."""
from __future__ import annotations

import json
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

CONFIG_FILE = Path(os.environ.get("CONFIG_FILE", Path.home() / ".config/imessage-archive.env"))
STATE_FILE = Path.home() / ".config/imessage-archive-agent.json"
INSTALL_DIR = Path.home() / ".local/imessage-archive"
POLL_SECONDS = int(os.environ.get("AGENT_POLL_SECONDS", "60"))
CANCEL_POLL_SECONDS = float(os.environ.get("AGENT_CANCEL_POLL_SECONDS", "2"))
PROGRESS_TAIL = 240
PROGRESS_REPORT_MIN_SECONDS = float(os.environ.get("AGENT_PROGRESS_REPORT_SECONDS", "1.5"))

# PROGRESS bytes_done=123 bytes_total=456 phase=sync Copying…
PROGRESS_LINE_RE = re.compile(
    r"^PROGRESS\s+bytes_done=(?P<done>\d+)\s+bytes_total=(?P<total>\d+)"
    r"(?:\s+phase=(?P<phase>\S+))?(?:\s+(?P<msg>.*))?$"
)
# rsync --info=progress2: "  1,234,567  45%  12.34MB/s    0:01:23"
RSYNC_PROGRESS_RE = re.compile(r"^\s*([\d,]+)\s+(\d+)%")
# Immich [####]  45.0%  12/100  …
IMMICH_PROGRESS_RE = re.compile(r"Immich\s+\[.*?\]\s+(\d+(?:\.\d+)?)%\s+(\d+)/(\d+)")


def load_config() -> dict[str, str]:
    cfg: dict[str, str] = {}
    if CONFIG_FILE.exists():
        for line in CONFIG_FILE.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            cfg[k.strip()] = os.path.expandvars(v.strip().strip('"'))
    cfg.setdefault("SERVER_URL", cfg.get("SEARCH_API", "http://192.168.1.200:8095").rstrip("/"))
    return cfg


def api_request(url: str, method: str = "GET", body: dict | None = None, token: str | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def register(server: str) -> dict:
    hostname = socket.gethostname()
    name = os.environ.get("CLIENT_NAME", hostname)
    return api_request(
        f"{server}/api/clients/register",
        method="POST",
        body={"name": name, "hostname": hostname},
    )


def ensure_registered(server: str) -> tuple[str, str]:
    state = load_state()
    if state.get("token") and state.get("server") == server:
        return state["client_id"], state["token"]
    reg = register(server)
    state = {"server": server, "client_id": reg["id"], "token": reg["token"], "name": reg["name"]}
    save_state(state)
    print(f"Registered as {reg['name']} ({reg['id']})")
    return reg["id"], reg["token"]


def report_status(
    server: str,
    token: str,
    run_id: str,
    status: str | None = None,
    phase: str | None = None,
    message: str | None = None,
    bytes_done: int | None = None,
    bytes_total: int | None = None,
) -> None:
    body: dict = {"run_id": run_id, "status": status, "phase": phase, "message": message}
    if bytes_done is not None:
        body["bytes_done"] = int(bytes_done)
    if bytes_total is not None:
        body["bytes_total"] = int(bytes_total)
    api_request(
        f"{server}/api/clients/backup/status",
        method="POST",
        token=token,
        body=body,
    )


def cancel_requested(server: str, token: str) -> bool:
    try:
        resp = api_request(
            f"{server}/api/clients/backup/cancel-check",
            method="POST",
            token=token,
            body={},
        )
        return bool(resp.get("cancel"))
    except Exception:  # noqa: BLE001
        return False


def _kill_process_group(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=8)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait(timeout=5)


def parse_progress_line(
    line: str,
    *,
    bytes_done: int | None,
    bytes_total: int | None,
    phase: str | None,
) -> tuple[int | None, int | None, str | None, str]:
    """Extract bytes/phase from a backup log line. Returns (done, total, phase, message)."""
    msg = line.strip()
    m = PROGRESS_LINE_RE.match(msg)
    if m:
        return (
            int(m.group("done")),
            int(m.group("total")),
            m.group("phase") or phase,
            (m.group("msg") or "").strip() or msg,
        )

    m = IMMICH_PROGRESS_RE.search(msg)
    if m and bytes_total:
        pct = float(m.group(1))
        # Map Immich % onto remaining mid-bucket if we already have a total.
        return int(bytes_total * (pct / 100.0)), bytes_total, phase or "immich", msg

    m = RSYNC_PROGRESS_RE.match(msg)
    if m:
        transferred = int(m.group(1).replace(",", ""))
        pct = int(m.group(2))
        if bytes_total and pct > 0:
            # Prefer percent of known total when available.
            return int(bytes_total * (pct / 100.0)), bytes_total, phase or "sync", msg
        if bytes_total:
            return min(transferred, bytes_total), bytes_total, phase or "sync", msg
        return transferred, bytes_total, phase or "sync", msg

    return bytes_done, bytes_total, phase, msg


def run_backup(server: str, token: str, triggered_by: str, schedule_id: str | None = None) -> None:
    try:
        start = api_request(
            f"{server}/api/clients/backup/start",
            method="POST",
            token=token,
            body={"triggered_by": triggered_by, "schedule_id": schedule_id},
        )
    except urllib.error.HTTPError as exc:
        if exc.code == 409:
            print("Backup cancelled before start")
            return
        raise

    run_id = start["run_id"]
    script = INSTALL_DIR / "client/export-and-sync.sh"
    if not script.exists():
        report_status(server, token, run_id, status="error", message="export-and-sync.sh not found")
        return

    report_status(server, token, run_id, status="running", phase="export", message="Starting backup")
    env = os.environ.copy()
    env["SERVER_URL"] = server
    env["CLIENT_TOKEN"] = token
    env["BACKUP_RUN_ID"] = run_id
    env["IMESSAGE_ARCHIVE_PROGRESS"] = "1"
    env["PATH"] = os.pathsep.join([
        "/opt/homebrew/bin",
        "/usr/local/bin",
        str(Path.home() / "bin"),
        env.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin"),
    ])

    log_path = Path.home() / "imessage-export" / "logs" / f"agent-{run_id}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    last_progress = ""
    bytes_done: int | None = None
    bytes_total: int | None = None
    phase = "export"
    stop = threading.Event()
    cancelled = threading.Event()
    last_report_at = 0.0
    lock = threading.Lock()

    def maybe_report(force: bool = False) -> None:
        nonlocal last_report_at
        now = time.time()
        if not force and (now - last_report_at) < PROGRESS_REPORT_MIN_SECONDS:
            return
        last_report_at = now
        try:
            report_status(
                server,
                token,
                run_id,
                status="running",
                phase=phase,
                message=last_progress,
                bytes_done=bytes_done,
                bytes_total=bytes_total,
            )
        except Exception:  # noqa: BLE001
            pass

    def forward_progress(pipe, log_fh) -> None:
        nonlocal last_progress, bytes_done, bytes_total, phase
        for raw in iter(pipe.readline, ""):
            if not raw:
                break
            log_fh.write(raw)
            log_fh.flush()
            sys.stdout.write(raw)
            sys.stdout.flush()
            line = raw.strip()
            if not line:
                continue
            with lock:
                is_progress = bool(PROGRESS_LINE_RE.match(line))
                is_rsync = bool(RSYNC_PROGRESS_RE.match(line))
                is_immich = bool(IMMICH_PROGRESS_RE.search(line))
                bd, bt, ph, msg = parse_progress_line(
                    line, bytes_done=bytes_done, bytes_total=bytes_total, phase=phase
                )
                if is_progress or is_rsync or is_immich:
                    bytes_done, bytes_total, phase = bd, bt, ph or phase
                    last_progress = msg[-PROGRESS_TAIL:]
                    maybe_report()
                elif line.startswith("[") and "ERROR" in line:
                    # Keep preflight / hard-failure log lines for the dashboard.
                    last_progress = line[-PROGRESS_TAIL:]
                    maybe_report(force=True)

    def cancel_watch(proc: subprocess.Popen) -> None:
        while not stop.wait(CANCEL_POLL_SECONDS):
            if proc.poll() is not None:
                return
            if cancel_requested(server, token):
                print("Cancel requested — stopping backup", flush=True)
                cancelled.set()
                _kill_process_group(proc)
                return

    try:
        with log_path.open("w", encoding="utf-8") as log_fh:
            proc = subprocess.Popen(
                ["/bin/bash", str(script)],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,
                bufsize=1,
            )
            watcher = threading.Thread(target=cancel_watch, args=(proc,), daemon=True)
            forwarder = threading.Thread(target=forward_progress, args=(proc.stdout, log_fh), daemon=True)
            watcher.start()
            forwarder.start()
            code = proc.wait()
            stop.set()
            forwarder.join(timeout=2)
            watcher.join(timeout=1)

        if cancelled.is_set() or code in (-signal.SIGTERM, -signal.SIGKILL, 130, 143):
            report_status(
                server,
                token,
                run_id,
                status="error",
                phase="cancelled",
                message=last_progress or "Stopped from dashboard",
                bytes_done=bytes_done,
                bytes_total=bytes_total,
            )
            print("Backup cancelled")
            return

        if code == 0:
            report_status(
                server,
                token,
                run_id,
                status="success",
                phase="done",
                message="Backup completed",
                bytes_done=bytes_total if bytes_total else bytes_done,
                bytes_total=bytes_total,
            )
            print("Backup completed successfully")
            return

        msg = last_progress or (
            f"export-and-sync.sh exited with code {code} "
            "(check Full Disk Access for /usr/bin/python3)"
        )
        report_status(
            server,
            token,
            run_id,
            status="error",
            phase="failed",
            message=msg[-2000:],
            bytes_done=bytes_done,
            bytes_total=bytes_total,
        )
        print(f"Backup failed: {msg}", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001
        report_status(server, token, run_id, status="error", message=str(exc))


def poll_once(server: str, token: str) -> None:
    hb = api_request(f"{server}/api/clients/heartbeat", method="POST", token=token, body={})
    if hb.get("cancel_backup"):
        # No in-process backup here; acknowledge so pending_cancels does not stick.
        cancel_requested(server, token)
    if hb.get("trigger_backup"):
        reason = hb.get("trigger_reason") or "schedule"
        print(f"Backup triggered ({reason})")
        run_backup(server, token, reason, hb.get("schedule_id"))


def main() -> int:
    cfg = load_config()
    server = cfg["SERVER_URL"].rstrip("/")
    print(f"iMessage Archive agent → {server}")

    try:
        _, token = ensure_registered(server)
    except urllib.error.URLError as exc:
        print(f"Cannot reach server: {exc}", file=sys.stderr)
        return 1

    if "--once" in sys.argv:
        poll_once(server, token)
        return 0

    print(f"Polling every {POLL_SECONDS}s (Ctrl+C to stop)")
    while True:
        try:
            poll_once(server, token)
        except Exception as exc:  # noqa: BLE001
            print(f"Poll error: {exc}", file=sys.stderr)
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    raise SystemExit(main())
