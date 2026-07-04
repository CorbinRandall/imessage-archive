# Agent / developer workflow

Local Git is the source of truth. Pushing to GitHub is optional.

## Repository locations

| Path | Role |
|------|------|
| `/Users/corbin/dev/imessage-archive` | Primary dev clone (commit here) |
| `~/.local/imessage-archive` | Mac agent install copy (`git pull` to update scripts) |
| `/mnt/user/appdata/imessage-archive` | Unraid server deploy (pull + rebuild) |

Keep dev clone and deploy paths in sync via `git pull` after commits.

## Branching

- `main` — stable, working state
- Feature/fix work: `git checkout -b fix/short-description` or `feat/short-description`
- Merge back to `main` when tested; delete the branch after merge

```bash
git checkout -b fix/immich-thumbnails
# ... edit, commit ...
git checkout main && git merge fix/immich-thumbnails
git branch -d fix/immich-thumbnails
```

## Before every commit

1. Never commit `.env`, exports, API keys, or `messages.jsonl`
2. Run checks (or install hooks once):

```bash
pip install ruff pytest
ruff check server client
cd server && pytest tests/ -v
shellcheck scripts/*.sh client/*.sh   # if shellcheck installed
```

3. One logical change per commit; imperative subject line

Install optional pre-commit hook:

```bash
./scripts/install-git-hooks.sh
```

## Secrets

- Mac: `~/.config/imessage-archive.env`
- Server: `/mnt/user/appdata/imessage-archive/.env`
- Template only in repo: `config/env.example`

## Deploy after merge to main

```bash
# Server
ssh root@192.168.1.200 'cd /mnt/user/appdata/imessage-archive && git pull && docker build --network=host -t imessage-archive:latest server/ && docker compose -f server/docker-compose.yml up -d --force-recreate imessage-archive'

# Mac agent scripts
cd ~/.local/imessage-archive && git pull
```

## GitHub (optional)

Remote is configured (`origin` → `CorbinRandall/imessage-archive`). Push when ready:

```bash
git push -u origin main
git push origin fix/my-branch   # feature branches
```

CI runs on push/PR via `.github/workflows/ci.yml`.

## Wolf Leader

Project slug: `imessage-archive`. Checkpoint with `/save` at session end.
