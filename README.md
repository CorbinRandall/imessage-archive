# iMessage Archive

A self-hosted server app for backing up, browsing, and searching your iMessage history. Runs on Unraid (Docker) with Mac clients that sync on a schedule you control from the web UI.

**Live UI:** `http://<your-server>:8095`

## Features

- **Web dashboard** — backup status, Mac client health, manual triggers
- **Browse conversations** — read messages with inline photos, videos, and audio
- **Semantic search** — vector search across all message text
- **Server-managed schedules** — set weekday/time backups per Mac from the GUI
- **Mac agent** — lightweight background process polls the server and runs backups automatically

## Architecture

```
┌──────────────── Mac ─────────────────┐
│  agent.py (launchd, polls server)    │
│  export-and-sync.sh                    │
│    → imessage-exporter (HTML+media)    │
│    → export-to-jsonl.py                │
│    → rsync to Unraid SMB               │
└────────────────┬───────────────────────┘
                 │
┌────────────────▼───────────────────────┐
│  Unraid Docker: imessage-archive       │
│    FastAPI + SQLite (schedules/clients)│
│    Qdrant (vector search)              │
│    /mnt/user/Misc/imessage-backup/     │
└────────────────────────────────────────┘
```

## Quick start

### Server (Unraid)

```bash
ssh root@192.168.1.200
curl -fsSL https://raw.githubusercontent.com/CorbinRandall/imessage-archive/main/scripts/bootstrap-server.sh | bash
```

Open **http://192.168.1.200:8095**

### Mac client

```bash
curl -fsSL https://raw.githubusercontent.com/CorbinRandall/imessage-archive/main/scripts/install-client.sh | bash
```

1. Grant **Full Disk Access** to Terminal (System Settings → Privacy & Security)
2. Your Mac appears on the dashboard within ~60 seconds
3. Go to **Schedules** tab → pick weekdays + time → Save

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

**Server** — `/mnt/user/appdata/imessage-archive/.env`  
**Mac** — `~/.config/imessage-archive.env`

```bash
SERVER_URL=http://192.168.1.200:8095
COPY_METHOD=clone    # clone (fast) | full (converts media) | disabled
```

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
| `GET /api/chats` | List conversations |
| `GET /api/chats/{id}/messages` | Messages + attachment metadata |
| `GET /api/media/{path}` | Serve photo/video/audio files |
| `GET /api/search?q=...` | Vector search |
| `POST /api/clients/{id}/backup/trigger` | Queue backup for Mac |
| `PUT /api/clients/{id}/schedule` | Set backup schedule |

## Requirements

- **Mac:** macOS, Homebrew, imessage-exporter, Full Disk Access
- **Server:** Unraid or Linux + Docker, SMB share, ~2 GB RAM

## License

MIT
