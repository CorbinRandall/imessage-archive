from __future__ import annotations

import json
from typing import Any

from app.config import ATTACHMENTS_DIR, CONTACTS_PATH, DATA_DIR, HTML_ATTACHMENTS_DIR, HTML_DIR, JSONL_PATH, RAW_DIR


def load_contacts() -> dict[str, str]:
    if not CONTACTS_PATH.exists():
        return {}
    return json.loads(CONTACTS_PATH.read_text(encoding="utf-8"))


def resolve_display_name(value: str) -> str:
    contacts = load_contacts()
    if not value or value == "Me":
        return value
    if value in contacts:
        return contacts[value]
    digits = "".join(c for c in value if c.isdigit())
    if len(digits) >= 10:
        tail = digits[-10:]
        for key, name in contacts.items():
            key_digits = "".join(c for c in key if c.isdigit())
            if key_digits.endswith(tail):
                return name
    return value


def load_messages() -> list[dict[str, Any]]:
    if not JSONL_PATH.exists():
        return []
    messages: list[dict[str, Any]] = []
    with JSONL_PATH.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                messages.append(json.loads(line))
    return messages


def list_chats() -> list[dict[str, Any]]:
    messages = load_messages()
    chats: dict[str, dict[str, Any]] = {}
    for msg in messages:
        key = str(msg.get("chat_id", msg.get("chat")))
        chat_name = resolve_display_name(msg.get("chat") or "")
        if key not in chats:
            chats[key] = {
                "chat_id": msg.get("chat_id"),
                "chat": chat_name,
                "participants": [resolve_display_name(p) for p in msg.get("participants", [])],
                "message_count": 0,
                "last_date": msg.get("date"),
            }
        chats[key]["message_count"] += 1
        if msg.get("date") and (not chats[key]["last_date"] or msg["date"] > chats[key]["last_date"]):
            chats[key]["last_date"] = msg["date"]
    return sorted(chats.values(), key=lambda c: c.get("last_date") or "", reverse=True)


def chat_messages(chat_id: int, limit: int = 500, offset: int = 0) -> list[dict[str, Any]]:
    result = []
    for msg in load_messages():
        if msg.get("chat_id") != chat_id:
            continue
        enriched = dict(msg)
        enriched["chat"] = resolve_display_name(msg.get("chat") or "")
        enriched["sender"] = resolve_display_name(msg.get("sender") or "")
        enriched["participants"] = [resolve_display_name(p) for p in msg.get("participants", [])]
        result.append(enriched)
    return result[offset : offset + limit]


def resolve_media_path(relative: str) -> Path | None:
    rel = relative.lstrip("/")
    candidates = [DATA_DIR / rel]

    if rel.startswith("raw/"):
        candidates.append(DATA_DIR / rel)
    else:
        candidates.append(RAW_DIR / rel.replace("raw/", "", 1))

    if rel.startswith("html-export/"):
        candidates.append(DATA_DIR / rel)
    else:
        candidates.append(HTML_DIR / rel.replace("html-export/", "", 1))

    for candidate in candidates:
        try:
            resolved = candidate.resolve()
            resolved.relative_to(DATA_DIR.resolve())
        except (ValueError, OSError):
            continue
        if resolved.exists() and resolved.is_file():
            return resolved

    name = Path(relative).name
    for root in (ATTACHMENTS_DIR, HTML_ATTACHMENTS_DIR, HTML_DIR):
        if not root.exists():
            continue
        for path in root.rglob(name):
            if path.is_file():
                return path
    return None


def list_html_exports() -> list[dict[str, str]]:
    if not HTML_DIR.exists():
        return []
    return [
        {"name": path.stem, "filename": path.name, "url": f"/api/html/{path.name}"}
        for path in sorted(HTML_DIR.glob("*.html"))
    ]


def archive_stats() -> dict[str, Any]:
    messages = load_messages()
    attachment_count = sum(len(m.get("attachments") or []) for m in messages)
    html_count = len(list(HTML_DIR.glob("*.html"))) if HTML_DIR.exists() else 0
    raw_size = sum(f.stat().st_size for f in RAW_DIR.rglob("*") if f.is_file()) if RAW_DIR.exists() else 0
    html_media = sum(1 for _ in HTML_ATTACHMENTS_DIR.rglob("*") if _.is_file()) if HTML_ATTACHMENTS_DIR.exists() else 0
    return {
        "message_count": len(messages),
        "chat_count": len(list_chats()),
        "attachment_count": attachment_count,
        "html_export_count": html_count,
        "html_media_count": html_media,
        "raw_bytes": raw_size,
        "contact_count": len(load_contacts()),
        "jsonl_exists": JSONL_PATH.exists(),
    }
