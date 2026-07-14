# Agent / developer workflow — iMessage Archive

Local Git is the source of truth. Pushing to GitHub is optional.

**Wolf Leader hub:** Proxmox `http://192.168.1.221:6971` (NOT Unraid, NOT localhost).  
MCP: `http://192.168.1.221:6972/mcp` · Project slug: `imessage-archive` · Checkpoint with `/save`.

## Repository locations

| Path | Role |
|------|------|
| `/Users/corbin/dev/imessage-archive` | Primary Mac dev clone (commit here) |
| `~/.local/imessage-archive` | Mac agent install copy (`git pull` to update scripts) |
| `/mnt/user/appdata/imessage-archive` | Unraid server deploy (pull + rebuild) |

Keep clones in sync via `git pull` after commits. Unraid hosts the **app**; Proxmox hosts **Wolf Leader** only.

## Branching

- `main` — stable, working state
- Feature/fix: `git checkout -b fix/short-description` or `feat/short-description`
- Merge back to `main` when tested; delete the branch after merge

## Before every commit

1. Never commit `.env`, exports, API keys, or `messages.jsonl`
2. Run checks (or install hooks once):

```bash
pip install ruff pytest
ruff check server client
cd server && pytest tests/ -v
shellcheck scripts/*.sh client/*.sh   # if shellcheck installed
./scripts/install-git-hooks.sh        # once — pre-commit Ruff/shellcheck
```

## Secrets

- Mac: `~/.config/imessage-archive.env`
- Server: `/mnt/user/appdata/imessage-archive/.env`
- Template only in repo: `config/env.example`
- Immich API key needs **Assets → View + Download** (not just Read) for chat thumbnails

## Deploy after merge to main

```bash
# Server (Unraid)
ssh root@192.168.1.200 'cd /mnt/user/appdata/imessage-archive && git pull && docker build --network=host -t imessage-archive:latest server/ && docker compose -f server/docker-compose.yml up -d --force-recreate imessage-archive'

# Mac agent scripts
cd ~/.local/imessage-archive && git pull
```

## GitHub (optional)

```bash
git push -u origin main
```
