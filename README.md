# iMessage Archive

Back up your iMessage history from a Mac to a home server (Unraid), browse conversations as HTML, and search them with semantic vector search.

## Architecture

```
Mac (client)                          Unraid (server)
┌─────────────────────┐              ┌──────────────────────────────────┐
│  Messages/chat.db   │   rsync/SMB  │  /mnt/user/Misc/imessage-backup  │
│  imessage-exporter  │ ──────────►  │    messages.jsonl  (search)      │
│  export-to-jsonl    │              │    html-export/    (browse)      │
└─────────────────────┘              │    raw/            (chat.db)     │
                                     │                                  │
                                     │  Docker:                         │
                                     │    Qdrant (vector DB)            │
                                     │    imessage-search (FastAPI UI)  │
                                     └──────────────────────────────────┘
```

## Quick start

### 1. Server (Unraid)

SSH into your server and run:

```bash
curl -fsSL https://raw.githubusercontent.com/CorbinRandall/imessage-archive/main/scripts/bootstrap-server.sh | bash
```

Or manually:

```bash
git clone https://github.com/CorbinRandall/imessage-archive.git /mnt/user/appdata/imessage-archive
cp /mnt/user/appdata/imessage-archive/config/env.example /mnt/user/appdata/imessage-archive/.env
# edit .env if needed
/mnt/user/appdata/imessage-archive/scripts/install-server.sh
```

Open **http://192.168.1.200:8095** for the search UI.

### 2. Client (Mac)

```bash
curl -fsSL https://raw.githubusercontent.com/CorbinRandall/imessage-archive/main/scripts/install-client.sh | bash
```

Then grant **Full Disk Access** to Terminal:

> System Settings → Privacy & Security → Full Disk Access → add Terminal

Run your first backup:

```bash
imessage-backup
```

This exports messages, syncs to the server, and triggers a vector reindex.

## What gets backed up

| Output | Location on server | Purpose |
|--------|-------------------|---------|
| `messages.jsonl` | `imessage-backup/messages.jsonl` | Vector search index |
| `html-export/` | `imessage-backup/html-export/` | Browse conversations in a browser |
| `raw/chat.db` | `imessage-backup/raw/` | Raw database for re-export |

## Search

The search UI at `http://<server>:8095` supports semantic queries:

- "dentist appointment next week"
- "wifi password"
- "what did we decide about the trip"

Results are ranked by meaning, not just keyword match.

API endpoints:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Search web UI |
| `/search?q=...` | GET | JSON search results |
| `/index` | POST | Rebuild vector index |
| `/health` | GET | Health check |
| `/api/stats` | GET | Index status |

## Configuration

Copy `config/env.example` to:

- **Server:** `/mnt/user/appdata/imessage-archive/.env`
- **Mac:** `~/.config/imessage-archive.env`

Key settings:

```bash
UNRAID_HOST=192.168.1.200      # server IP
UNRAID_SHARE=Misc              # SMB share name
SEARCH_API=http://192.168.1.200:8095
COPY_METHOD=clone              # clone (fast) | full (converts media) | disabled
```

## Automation

The client installer sets up a monthly backup (1st of each month, 3 AM) via launchd.

To change the schedule, edit `~/Library/LaunchAgents/com.imessage-archive.backup.plist`.

## Updating

**Server:**

```bash
cd /mnt/user/appdata/imessage-archive && git pull
./scripts/install-server.sh
```

**Client:**

```bash
~/.local/imessage-archive/scripts/install-client.sh
```

## Requirements

### Mac
- macOS with Messages signed into your Apple ID
- Homebrew
- [imessage-exporter](https://github.com/ReagentX/imessage-exporter)
- Full Disk Access for Terminal

### Server
- Unraid (or any Linux host with Docker)
- SMB share for backup data
- ~2 GB RAM for the embedding model

## Troubleshooting

**"Unable to read chat database"** — Grant Full Disk Access to Terminal.

**Search returns no results** — Run `imessage-backup` on your Mac, then click **Reindex** in the UI or `curl -X POST http://<server>:8095/index`.

**Docker build fails on Unraid** — The install script uses `--network=host` for DNS. If it still fails, check your router DNS.

**Missing attachments in export** — Some attachments only exist on your phone. Make an encrypted iPhone backup and export from that separately with `imessage-exporter -p <backup-path> -a iOS`.

## License

MIT
