from __future__ import annotations

import json
import re
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.config import ATTACHMENTS_DIR, CONTACTS_PATH, DATA_DIR, HTML_ATTACHMENTS_DIR, HTML_DIR, JSONL_PATH, RAW_DIR, STATE_DIR

_stats_cache: dict[str, Any] = {"at": 0.0, "data": {}}
_STATS_TTL = 60.0

_MEDIA_CACHE_DIR = STATE_DIR / "media-cache"


def convert_image_for_web(source: Path) -> Path | None:
    """Convert HEIC/TIFF to browser-friendly JPEG, cached on disk."""
    try:
        import hashlib

        from PIL import Image
        import pillow_heif

        pillow_heif.register_heif_opener()
    except ImportError:
        return None

    _MEDIA_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(f"{source}:{source.stat().st_mtime_ns}".encode()).hexdigest()
    cached = _MEDIA_CACHE_DIR / f"{key}.jpg"
    if cached.exists():
        return cached
    try:
        with Image.open(source) as img:
            img = img.convert("RGB")
            img.thumbnail((2048, 2048))
            img.save(cached, "JPEG", quality=85)
        return cached
    except Exception:
        return None


def convert_audio_for_web(source: Path) -> Path | None:
    """Transcode CAF (Apple voice messages) to M4A for browser playback, cached on disk."""
    import hashlib
    import subprocess

    _MEDIA_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(f"{source}:{source.stat().st_mtime_ns}".encode()).hexdigest()
    cached = _MEDIA_CACHE_DIR / f"{key}.m4a"
    if cached.exists():
        return cached
    tmp = cached.with_suffix(".tmp.m4a")
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(source), "-c:a", "aac", "-b:a", "96k", str(tmp)],
            check=True, capture_output=True, timeout=120,
        )
        tmp.rename(cached)
        return cached
    except Exception:
        tmp.unlink(missing_ok=True)
        return None


def _normalize_phone(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    if len(digits) == 11 and digits.startswith("1"):
        return digits[1:]
    return digits


@lru_cache(maxsize=1)
def _contacts_data() -> tuple[dict[str, str], dict[str, str]]:
    if not CONTACTS_PATH.exists():
        return {}, {}
    raw: dict[str, str] = json.loads(CONTACTS_PATH.read_text(encoding="utf-8"))
    by_phone: dict[str, str] = {}
    for key, name in raw.items():
        tail = _normalize_phone(key)
        if len(tail) >= 10:
            by_phone[tail[-10:]] = name
    return raw, by_phone


def load_contacts() -> dict[str, str]:
    raw, _ = _contacts_data()
    return raw


def resolve_display_name(value: str) -> str:
    if not value or value == "Me":
        return value
    raw, by_phone = _contacts_data()
    if value in raw:
        return raw[value]
    tail = _normalize_phone(value)
    if len(tail) >= 10 and tail[-10:] in by_phone:
        return by_phone[tail[-10:]]
    email = value.strip().lower()
    if email in raw:
        return raw[email]
    return value


def _jsonl_mtime() -> float:
    try:
        return JSONL_PATH.stat().st_mtime
    except OSError:
        return 0.0


@lru_cache(maxsize=2)
def _messages_cached(mtime: float) -> tuple[dict[str, Any], ...]:
    if not JSONL_PATH.exists():
        return tuple()
    messages: list[dict[str, Any]] = []
    with JSONL_PATH.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                messages.append(json.loads(line))
    return tuple(messages)


def load_messages() -> list[dict[str, Any]]:
    return list(_messages_cached(_jsonl_mtime()))


def list_chats() -> list[dict[str, Any]]:
    chats: dict[str, dict[str, Any]] = {}
    for msg in load_messages():
        key = str(msg.get("chat_id", msg.get("chat")))
        if key not in chats:
            chats[key] = {
                "chat_id": msg.get("chat_id"),
                "chat": msg.get("chat") or "Unknown",
                "participants": msg.get("participants") or [],
                "message_count": 0,
                "last_date": msg.get("date"),
            }
        chats[key]["message_count"] += 1
        if msg.get("date") and (not chats[key]["last_date"] or msg["date"] > chats[key]["last_date"]):
            chats[key]["last_date"] = msg["date"]
    return sorted(chats.values(), key=lambda c: c.get("last_date") or "", reverse=True)


def chat_messages(chat_id: int, limit: int = 500, offset: int = 0) -> list[dict[str, Any]]:
    result = [msg for msg in load_messages() if msg.get("chat_id") == chat_id]
    # Return the newest `limit` messages (offset pages backwards in time),
    # preserving chronological order for rendering.
    end = len(result) - offset
    start = max(0, end - limit)
    return result[start:end] if end > 0 else []


def resolve_media_path(relative: str) -> Path | None:
    rel = relative.lstrip("/")
    candidates = [DATA_DIR / rel, RAW_DIR / rel.removeprefix("raw/"), HTML_DIR / rel.removeprefix("html-export/")]

    for candidate in candidates:
        try:
            resolved = candidate.resolve()
            resolved.relative_to(DATA_DIR.resolve())
        except (ValueError, OSError):
            continue
        if resolved.exists() and resolved.is_file():
            return resolved

    name = Path(relative).name
    for root in (HTML_ATTACHMENTS_DIR, ATTACHMENTS_DIR):
        if not root.exists():
            continue
        for path in root.glob(f"**/{name}"):
            if path.is_file():
                return path
    return None


def media_gallery(kind: str = "all", limit: int = 200, offset: int = 0) -> dict[str, Any]:
    """All media attachments across chats, newest first."""
    items: list[dict[str, Any]] = []
    for msg in load_messages():
        for att in msg.get("attachments") or []:
            mime = att.get("mime_type") or ""
            name = (att.get("name") or "").lower()
            if mime.startswith("image/gif") or name.endswith(".gif"):
                media_kind = "gif"
            elif mime.startswith("image/"):
                media_kind = "photo"
            elif mime.startswith("video/"):
                media_kind = "video"
            elif mime.startswith("audio/"):
                media_kind = "audio"
            else:
                continue
            if kind != "all" and media_kind != kind:
                continue
            items.append({
                "kind": media_kind,
                "name": att.get("name"),
                "mime_type": mime,
                "path": att.get("path"),
                "paths": att.get("paths") or [att.get("path")],
                "chat": msg.get("chat"),
                "chat_id": msg.get("chat_id"),
                "sender": msg.get("sender"),
                "date": msg.get("date"),
            })
    items.sort(key=lambda i: i.get("date") or "", reverse=True)
    total = len(items)
    return {"total": total, "items": items[offset : offset + limit]}


def list_html_exports() -> list[dict[str, str]]:
    if not HTML_DIR.exists():
        return []
    return [
        {"name": path.stem, "filename": path.name, "url": f"/api/html/{path.name}"}
        for path in sorted(HTML_DIR.glob("*.html"))
    ]


def _count_files(root: Path) -> int:
    if not root.exists():
        return 0
    try:
        return sum(1 for p in root.rglob("*") if p.is_file())
    except OSError:
        return 0


def archive_stats() -> dict[str, Any]:
    global _stats_cache
    now = time.time()
    if _stats_cache["data"] and now - _stats_cache["at"] < _STATS_TTL:
        return _stats_cache["data"]

    messages = load_messages()
    chats = list_chats()
    data = {
        "message_count": len(messages),
        "chat_count": len(chats),
        "attachment_count": sum(len(m.get("attachments") or []) for m in messages),
        "html_export_count": len(list(HTML_DIR.glob("*.html"))) if HTML_DIR.exists() else 0,
        "html_media_count": _count_files(HTML_ATTACHMENTS_DIR),
        "raw_bytes": 0,
        "contact_count": len(load_contacts()),
        "jsonl_exists": JSONL_PATH.exists(),
    }
    _stats_cache = {"at": now, "data": data}
    return data


def invalidate_caches() -> None:
    global _stats_cache
    _stats_cache = {"at": 0.0, "data": {}}
    _messages_cached.cache_clear()
    _contacts_data.cache_clear()
