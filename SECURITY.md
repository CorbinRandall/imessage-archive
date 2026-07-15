# Security Policy

## Supported versions

| Version | Supported |
| ------- | --------- |
| `main`  | Yes       |

There are no tagged releases yet; security fixes land on `main`.

## Reporting a vulnerability

**Do not open a public GitHub issue for security bugs.**

1. Use [GitHub Security Advisories](https://github.com/CorbinRandall/imessage-archive/security/advisories/new) to report privately, or
2. Contact the maintainer via GitHub if advisories are unavailable.

Include steps to reproduce, impact, and any suggested fix if you have one.

## Sensitive data

This project handles **personal iMessage content**. When reporting bugs:

- Redact message text, phone numbers, and attachment filenames where possible.
- Never paste `.env` files, Immich API keys, or client bearer tokens.

## Scope notes

- **In scope:** authentication bypass, remote code execution, path traversal in media APIs, secret leakage in logs or responses.
- **Out of scope:** issues requiring physical access to your Mac, Full Disk Access already granted to the backup agent, or misconfigured SMB/Immich credentials on your LAN.

We aim to acknowledge reports within a few days and patch critical issues on `main` promptly.
