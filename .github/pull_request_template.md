## Summary

<!-- What changed and why? Link related issues with "Fixes #123". -->

## Test plan

- [ ] CI passes locally or on this PR
- [ ] Server: `docker compose -f server/docker-compose.yml config`
- [ ] Mac client: `imessage-backup` (if client changes)
- [ ] Web UI manually checked (if UI changes)

## Checklist

- [ ] No secrets, `.env` values, or personal message data in the diff
- [ ] `config/env.example` updated if new env vars were added
- [ ] README updated if user-facing behavior changed
