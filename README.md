# iMessage Archive

A self-hosted server app for backing up, browsing, and searching your iMessage history. Runs on Unraid (Docker) with Mac clients that sync on a schedule you control from the web UI.

**Live UI:** `http://<your-server>:8095`

## Features

- **Web dashboard** — backup status, Mac client health, manual triggers
- **Browse conversations** — read messages with inline photos, videos, and audio
- **Semantic search** — vector search across all message text (Qdrant + GPU embeddings)
- **Immich integration** — optional photo/video storage and thumbnail proxy (no API key in the browser)
- **Server-managed schedules** — set weekday/time backups per Mac from the GUI
- **Mac agent** — lightweight background process polls the server and runs backups automatically

## Architecture

```
┌──────────────── Mac ─────────────────────────────┐
│  agent.py (launchd, polls server)              │
│  export-and-sync.sh                              │
│    → imessage-exporter (HTML + local attachments)│
│    → upload-to-immich.py (optional)              │
│    → export-to-jsonl.py                          │
│    → rsync JSONL + contacts to Unraid SMB        │
└────────────────┬─────────────────────────────────┘
                 │ HTTPS + Bearer token
┌────────────────▼─────────────────────────────────┐
│  Unraid Docker stack (server/docker-compose.yml) │
│    imessage-archive — FastAPI + SQLite           │
│    qdrant — vector search                        │
│    /mnt/user/<share>/imessage-backup/  (data)    │
│    /mnt/cache/.../qdrant/              (vectors)│
└──────────────────────────────────────────────────┘
                 │ optional x-api-key (server-side only)
┌────────────────▼─────────────────────────────────┐
│  Immich — thumbnails, originals, album "iMessage"│
└──────────────────────────────────────────────────┘
```

| Path | Purpose |
|------|---------|
| `client/` | Mac backup agent, export scripts, launchd plists |
| `server/` | FastAPI app, Dockerfile, docker-compose |
| `scripts/` | One-line install/bootstrap for server and Mac |
| `config/env.example` | Documented environment variables (copy to `.env`) |

## Quick start

### Server (Unraid)

```bash
ssh root@<your-unraid-host>
curl -fsSL https://raw.githubusercontent.com/CorbinRandall/imessage-archive/main/scripts/bootstrap-server.sh | bash
```

Open **http://\<your-unraid-host\>:8095**

The bootstrap script clones the repo to `/mnt/user/appdata/imessage-archive`, copies `config/env.example` → `.env`, and runs `scripts/install-server.sh`.

### Mac client

```bash
curl -fsSL https://raw.githubusercontent.com/CorbinRandall/imessage-archive/main/scripts/install-client.sh | bash
```

1. Grant **Full Disk Access** to Terminal (or `/bin/bash` if using launchd) — System Settings → Privacy & Security
2. Copy server settings into `~/.config/imessage-archive.env` (see [Configuration](#configuration))
3. Your Mac appears on the dashboard within ~60 seconds
4. Go to **Schedules** → pick weekdays + time → Save

## Using the GUI

| Tab | What it does |
|-----|----------------|
| **Dashboard** | Stats, connected Macs, backup now button, recent runs |
| **Browse** | Pick a conversation, view messages and media inline |
| **Search** | Semantic search ("dentist appointment", "wifi password") |
| **Schedules** | Per-Mac backup schedule (weekdays + time) |

## Manual backup

```bash
imessage-backup
```

Or click **Backup now** on the dashboard for a specific Mac.

## Configuration

Copy [`config/env.example`](config/env.example) and fill in values. **Never commit `.env`.**

| Variable | Where | Description |
|----------|-------|-------------|
| `SERVER_HOST` | Server `.env` | Unraid IP or hostname (for install script output) |
| `DATA_PATH` | Server `.env` | Host path mounted read-only into the container (`/data`) |
| `APP_PATH` | Server `.env` | Repo + state on host (`/mnt/user/appdata/imessage-archive`) |
| `SEARCH_PORT` | Server `.env` | Web UI port (default `8095`) |
| `QDRANT_PATH` | Server `.env` | **Must be on `/mnt/cache`**, not `/mnt/user` (FUSE breaks Qdrant mmap) |
| `IMMICH_URL` | Server + Mac | Immich base URL (optional; enables media proxy) |
| `IMMICH_API_KEY` | Server + Mac | Immich API key — [required scopes documented in env.example](config/env.example) |
| `IMMICH_ALBUM` | Server + Mac | Album name for uploads (default `iMessage`) |
| `SERVER_URL` | Mac | Archive server URL (`http://host:8095`) |
| `COPY_METHOD` | Mac | `clone` (fast) \| `full` (HEIC→JPEG, CAF→M4A) \| `disabled` |
| `SMB_USER` / `SMB_PASS` | Mac | Optional SMB credentials for Unraid share |

**Server** — `/mnt/user/appdata/imessage-archive/.env`  
**Mac** — `~/.config/imessage-archive.env`

When `IMMICH_API_KEY` is set, the Mac uploads photos/videos to Immich and the server proxies thumbnails so the browser never sees the key. Without Immich, attachments are served from the local SMB export.

## Deployment

Production stack is defined in [`server/docker-compose.yml`](server/docker-compose.yml):

- **Build:** `docker build -t imessage-archive:latest server/` (or `./scripts/install-server.sh`)
- **Run:** `docker compose -f server/docker-compose.yml up -d`
- **GPU:** NVIDIA runtime is used for embedding acceleration; CPU fallback exists if GPU is unavailable
- **Health:** `GET /health` (Docker `HEALTHCHECK` every 30s)
- **Data volume:** `${DATA_PATH}` → `/data` (read-only message export)
- **State volume:** `${APP_PATH}/state` → `/state` (SQLite clients/schedules)

After `git pull`, redeploy with:

```bash
cd /mnt/user/appdata/imessage-archive && ./scripts/install-server.sh
```

## Development

```bash
git clone https://github.com/CorbinRandall/imessage-archive.git
cd imessage-archive

# Lint
pip install ruff
ruff check server client

# Tests (SQLite layer only; no GPU/Qdrant)
pip install pytest
cd server && pytest tests/ -v

# Local Docker build
docker build -t imessage-archive:dev server
docker compose -f server/docker-compose.yml config
```

CI runs on every push/PR: Ruff, compileall, pytest, shellcheck, and Docker build. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Updating

```bash
# Server
cd /mnt/user/appdata/imessage-archive && git pull && ./scripts/install-server.sh

# Mac
~/.local/imessage-archive/scripts/install-client.sh
```

## API

| Endpoint | Description |
|----------|-------------|
| `GET /health` | Liveness check |
| `GET /api/chats` | List conversations |
| `GET /api/chats/{id}/messages` | Messages + attachment metadata |
| `GET /api/media/{path}` | Serve local photo/video/audio (non-Immich) |
| `GET /api/immich/{asset_id}/{size}` | Proxy Immich thumbnail/preview/original |
| `GET /api/search?q=...` | Vector search |
| `POST /api/clients/register` | Mac agent registration |
| `POST /api/clients/{id}/backup/trigger` | Queue backup for Mac |
| `PUT /api/clients/{id}/schedule` | Set backup schedule |

## Requirements

- **Mac:** macOS, Homebrew, [imessage-exporter](https://github.com/ReagentX/imessage-exporter), Full Disk Access
- **Server:** Unraid or Linux + Docker, NVIDIA GPU recommended, SMB share, ~2 GB RAM
- **Optional:** [Immich](https://immich.app/) for media storage and CDN-style delivery

## Security

- Client auth uses per-Mac bearer tokens stored in `~/.config/imessage-archive/agent-state.json` (local only).
- Immich API keys live in `.env` on server and Mac — never in git.
- See [SECURITY.md](SECURITY.md) for vulnerability reporting.

## License

MIT — see [LICENSE](LICENSE).
