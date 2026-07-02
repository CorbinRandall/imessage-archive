from __future__ import annotations

import json
import mimetypes
from collections import defaultdict
from pathlib import Path
from typing import Any

from app.config import ATTACHMENTS_DIR, DATA_DIR, HTML_DIR, JSONL_PATH, RAW_DIR


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
        if key not in chats:
            chats[key] = {
                "chat_id": msg.get("chat_id"),
                "chat": msg.get("chat"),
                "participants": msg.get("participants", []),
                "message_count": 0,
                "last_date": msg.get("date"),
            }
        chats[key]["message_count"] += 1
        if msg.get("date") and (not chats[key]["last_date"] or msg["date"] > chats[key]["last_date"]):
            chats[key]["last_date"] = msg["date"]
    return sorted(chats.values(), key=lambda c: c.get("last_date") or "", reverse=True)


def chat_messages(chat_id: int, limit: int = 500, offset: int = 0) -> list[dict[str, Any]]:
    messages = [m for m in load_messages() if m.get("chat_id") == chat_id]
    return messages[offset : offset + limit]


def resolve_media_path(relative: str) -> Path | None:
    rel = relative.lstrip("/")
    for base in (DATA_DIR, ATTACHMENTS_DIR.parent, HTML_DIR):
        candidate = (base / rel).resolve()
        try:
            candidate.relative_to(DATA_DIR.resolve())
        except ValueError:
            continue
        if candidate.exists() and candidate.is_file():
            return candidate
    # Try under Attachments by suffix match
    name = Path(relative).name
    if ATTACHMENTS_DIR.exists():
        for path in ATTACHMENTS_DIR.rglob(name):
            if path.is_file():
                return path
    return None


def list_html_exports() -> list[dict[str, str]]:
    if not HTML_DIR.exists():
        return []
    files = []
    for path in sorted(HTML_DIR.glob("*.html")):
        files.append({"name": path.stem, "filename": path.name, "url": f"/api/html/{path.name}"})
    return files


def archive_stats() -> dict[str, Any]:
    messages = load_messages()
    attachment_count = sum(len(m.get("attachments") or []) for m in messages)
    html_count = len(list(HTML_DIR.glob("*.html"))) if HTML_DIR.exists() else 0
    raw_size = sum(f.stat().st_size for f in RAW_DIR.rglob("*") if f.is_file()) if (DATA_DIR / "raw").exists() else 0
    return {
        "message_count": len(messages),
        "chat_count": len(list_chats()),
        "attachment_count": attachment_count,
        "html_export_count": html_count,
        "raw_bytes": raw_size,
        "jsonl_exists": JSONL_PATH.exists(),
    }
