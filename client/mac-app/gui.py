#!/usr/bin/env python3
"""Mac status UI for iMessage Archive — local page in the default browser (no tkinter)."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
import time
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

HOME = Path.home()
CONFIG_FILE = Path(os.environ.get("CONFIG_FILE", HOME / ".config/imessage-archive.env"))
STATE_FILE = HOME / ".config/imessage-archive-agent.json"
POLL = 4
PORT = int(os.environ.get("IMESSAGE_ARCHIVE_UI_PORT", "8765"))


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
    with urllib.request.urlopen(req, timeout=8) as resp:
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
    exporter = None
    for p in ("/opt/homebrew/bin/imessage-exporter", "/usr/local/bin/imessage-exporter"):
        if Path(p).exists():
            exporter = p
            break
    if not exporter:
        return False, "imessage-exporter not installed"
    try:
        r = subprocess.run([exporter, "-d"], capture_output=True, text=True, timeout=15)
        if r.returncode == 0:
            return True, "Full Disk Access OK"
        return False, "Full Disk Access needed — click Grant Permissions"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def open_fda_settings() -> None:
    subprocess.run(
        ["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"],
        check=False,
    )


def agent_loaded() -> bool:
    try:
        r = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=5)
        return "imessage-archive.agent" in (r.stdout or "")
    except Exception:
        return False


def run_backup_now(server: str) -> str:
    try:
        state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
        client_id = state.get("client_id")
        if client_id:
            api(f"{server}/api/clients/{client_id}/backup/trigger", "POST")
            return "Backup queued — starts within ~60s"
    except Exception as exc:  # noqa: BLE001
        return f"Could not queue backup: {exc}"
    return "Not registered yet"


def fetch_status(server: str, client_id: str, token: str) -> dict:
    out: dict = {"connected": False, "client": None, "run": None, "error": None}
    try:
        api(f"{server}/health")
        out["connected"] = True
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"Cannot reach server: {exc}"
        return out
    try:
        data = api(f"{server}/api/clients")
        for c in data.get("clients") or []:
            if c.get("id") == client_id:
                out["client"] = c
                break
        for r in data.get("runs") or []:
            if r.get("client_id") == client_id:
                out["run"] = r
                break
        try:
            api(f"{server}/api/clients/heartbeat", "POST", {}, token=token)
        except Exception:
            pass
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)
    return out


def fmt_time(ts) -> str:
    if not ts:
        return "never"
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(float(ts)))
    except Exception:
        return str(ts)


def alert(msg: str) -> None:
    safe = msg.replace("\\", "\\\\").replace('"', '\\"')[:900]
    subprocess.run(["osascript", "-e", f'display alert "iMessage Archive" message "{safe}"'], check=False)


def pick_port() -> int:
    for port in range(PORT, PORT + 20):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.bind(("127.0.0.1", port))
            s.close()
            return port
        except OSError:
            continue
    return PORT


def main() -> int:
    cfg = load_config()
    server_url = cfg["SERVER_URL"].rstrip("/")
    try:
        cid, token, name = ensure_registered(server_url)
    except Exception as exc:  # noqa: BLE001
        alert(f"Cannot reach {server_url}\n\n{exc}")
        return 1

    port = pick_port()
    httpd_holder: dict = {"s": None}

    def request_shutdown() -> None:
        def _stop() -> None:
            time.sleep(0.15)
            s = httpd_holder.get("s")
            if s is not None:
                s.shutdown()

        threading.Thread(target=_stop, daemon=True).start()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args) -> None:
            return

        def _json(self, code: int, payload: dict) -> None:
            body = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path == "/api/status":
                fda_ok, fda_msg = check_fda()
                st = fetch_status(server_url, cid, token)
                client = st.get("client") or {}
                run = st.get("run") or {}
                self._json(200, {
                    "name": name,
                    "server": server_url,
                    "fda": fda_msg,
                    "fda_ok": fda_ok,
                    "agent": agent_loaded(),
                    "connected": st.get("connected"),
                    "error": st.get("error"),
                    "last_seen": fmt_time(client.get("last_seen_at")),
                    "last_backup": fmt_time(client.get("last_backup_at")),
                    "last_status": client.get("last_status"),
                    "run_status": run.get("status"),
                    "run_phase": run.get("phase"),
                    "run_message": (run.get("message") or "")[:200],
                })
                return
            if path == "/api/backup":
                fda_ok, fda_msg = check_fda()
                if not fda_ok:
                    self._json(200, {"ok": False, "message": fda_msg})
                    return
                self._json(200, {"ok": True, "message": run_backup_now(server_url)})
                return
            if path == "/api/fda":
                open_fda_settings()
                self.send_response(204)
                self.end_headers()
                return
            if path == "/api/quit":
                self.send_response(204)
                self.end_headers()
                request_shutdown()
                return

            html = f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>iMessage Archive</title>
<style>
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    max-width: 440px; margin: 48px auto; padding: 0 20px;
    background: #0c0e12; color: #eef0f4;
  }}
  h1 {{ font-size: 1.4rem; margin: 0 0 .25rem; }}
  .sub {{ color: #8b93a7; font-size: .9rem; margin-bottom: 1.25rem; word-break: break-all; }}
  .card {{
    background: #181c25; border: 1px solid #2a3140; border-radius: 12px;
    padding: 1rem 1.1rem; margin-bottom: 1rem;
  }}
  .row {{ display: flex; align-items: center; gap: .6rem; font-size: 1.05rem; font-weight: 600; }}
  .dot {{ width: 10px; height: 10px; border-radius: 50%; background: #8b93a7; flex-shrink: 0; }}
  .dot.ok {{ background: #3ecf8e; }}
  .dot.bad {{ background: #f45b69; }}
  .dot.run {{ background: #f5a623; }}
  .meta {{ color: #8b93a7; font-size: .85rem; margin-top: .75rem; line-height: 1.5; white-space: pre-wrap; }}
  .actions {{ display: flex; flex-wrap: wrap; gap: .5rem; }}
  button, a.btn {{
    appearance: none; border: none; border-radius: 8px; padding: .65rem 1rem;
    font-weight: 600; font-size: .9rem; cursor: pointer; text-decoration: none;
    background: #4f8ff7; color: white;
  }}
  button.secondary, a.secondary {{ background: #1f2430; color: #eef0f4; border: 1px solid #2a3140; }}
  .hint {{ color: #8b93a7; font-size: .8rem; margin-top: 1.25rem; line-height: 1.4; }}
</style>
</head><body>
  <h1>iMessage Archive</h1>
  <div class="sub">{server_url}</div>
  <div class="card">
    <div class="row"><span class="dot" id="dot"></span><span id="status">Starting…</span></div>
    <div class="meta" id="meta"></div>
  </div>
  <div class="actions">
    <button onclick="doFda()">Grant Permissions</button>
    <button onclick="doBackup()">Backup Now</button>
    <a class="btn secondary" href="{server_url}" target="_blank" rel="noopener">Dashboard</a>
    <button class="secondary" onclick="doQuit()">Quit</button>
  </div>
  <p class="hint">Keep this tab open for live status. Schedules keep running in the background even if you quit.</p>
<script>
async function tick() {{
  try {{
    const d = await (await fetch('/api/status')).json();
    const dot = document.getElementById('dot');
    const status = document.getElementById('status');
    const meta = document.getElementById('meta');
    let cls = 'bad', text = 'Offline';
    if (d.connected) {{
      const rs = (d.run_status || '').toLowerCase();
      if (rs === 'running') {{
        cls = 'run';
        text = 'Backing up… ' + (d.run_phase || '');
      }} else {{
        cls = 'ok';
        text = 'Connected as ' + d.name;
      }}
    }}
    dot.className = 'dot ' + cls;
    status.textContent = text;
    meta.textContent = [
      'Permissions: ' + (d.fda || ''),
      'Background agent: ' + (d.agent ? 'running' : 'not loaded'),
      'Last seen: ' + (d.last_seen || 'never'),
      'Last backup: ' + (d.last_backup || 'never') + (d.last_status ? ' (' + d.last_status + ')' : ''),
      d.run_message ? ('Latest: ' + d.run_message) : '',
      d.error || ''
    ].filter(Boolean).join('\\n');
  }} catch (e) {{
    document.getElementById('dot').className = 'dot bad';
    document.getElementById('status').textContent = 'Status UI error';
  }}
}}
async function doFda() {{
  await fetch('/api/fda');
  alert('Enable Full Disk Access for:\\n\\n• iMessage Archive (if listed)\\n• python3\\n• imessage-exporter');
}}
async function doBackup() {{
  const d = await (await fetch('/api/backup')).json();
  alert(d.message || 'Done');
  tick();
}}
async function doQuit() {{ await fetch('/api/quit'); window.close(); }}
tick(); setInterval(tick, {POLL * 1000});
</script>
</body></html>"""
            body = html.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

    httpd = HTTPServer(("127.0.0.1", port), Handler)
    httpd_holder["s"] = httpd
    webbrowser.open(f"http://127.0.0.1:{port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
