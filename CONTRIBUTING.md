# Contributing

Thanks for your interest in improving iMessage Archive.

## Getting started

1. Clone the repo (or work in your existing clone at `~/dev/imessage-archive`).
2. Copy `config/env.example` to a local `.env` (never commit it).
3. Optional: `./scripts/install-git-hooks.sh` for pre-commit lint on staged files.
4. For server development, see [README — Development](README.md#development) and [AGENTS.md](AGENTS.md) for branching/deploy workflow.

## Local Git workflow

You do **not** need to push to GitHub to use Git. Work on branches locally and merge to `main` when ready:

```bash
git checkout -b fix/my-change
# edit, test, commit
git checkout main && git merge fix/my-change
```

To push later (optional): `git push -u origin main`

See [AGENTS.md](AGENTS.md) for repo paths, deploy steps, and agent checkpoints.

## Pull requests

- Keep PRs focused; one feature or fix per PR when possible.
- Ensure CI passes (`lint`, `test`, `shellcheck`, `docker build`).
- Update README and `config/env.example` when behavior or configuration changes.
- Do not commit message exports, API keys, or personal data.

## Commit messages

Use clear, imperative subjects (same style as existing history):

- `Fix Immich upload retry on 502`
- `Add schedule export to dashboard`

## Code style

- Python 3.12, formatted/linted with [Ruff](https://docs.astral.sh/ruff/) (`ruff check server client`).
- Bash scripts should pass `shellcheck`.
- Match existing patterns in `server/app/` and `client/`.

## Reporting security issues

See [SECURITY.md](SECURITY.md). Do not file public issues for vulnerabilities.
