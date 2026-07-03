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
_THUMB_MAX = 320
_FULL_MAX = 2048
_ffmpeg_hwaccels: list[str] | None = None


def _ffmpeg_hwaccel_list() -> list[str]:
    global _ffmpeg_hwaccels
    if _ffmpeg_hwaccels is not None:
        return _ffmpeg_hwaccels
    import subprocess

    try:
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-hwaccels"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        _ffmpeg_hwaccels = [line.strip() for line in proc.stdout.splitlines() if line.strip() and not line.startswith("Hardware")]
    except Exception:
        _ffmpeg_hwaccels = []
    return _ffmpeg_hwaccels


def gpu_media_available() -> bool:
    return "cuda" in _ffmpeg_hwaccel_list()


def _cache_key(source: Path, label: str) -> Path:
    import hashlib

    _MEDIA_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha1(f"{label}:{source}:{source.stat().st_mtime_ns}".encode()).hexdigest()
    return _MEDIA_CACHE_DIR / f"{digest}.jpg"


def _convert_image_pillow(source: Path, dest: Path, max_size: int) -> bool:
    try:
        from PIL import Image
        import pillow_heif

        pillow_heif.register_heif_opener()
    except ImportError:
        return False
    try:
        with Image.open(source) as img:
            img = img.convert("RGB")
            img.thumbnail((max_size, max_size))
            img.save(dest, "JPEG", quality=82 if max_size <= _THUMB_MAX else 85)
        return True
    except Exception:
        return False


def _convert_image_ffmpeg_gpu(source: Path, dest: Path, max_size: int) -> bool:
    """Use NVIDIA decode/scale when the container ffmpeg build supports cuda."""
    if "cuda" not in _ffmpeg_hwaccel_list():
        return False
    import subprocess

    vf = f"scale_cuda={max_size}:-2:force_original_aspect_ratio=decrease"
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-hwaccel", "cuda", "-hwaccel_output_format", "cuda",
        "-i", str(source),
        "-vf", vf,
        "-frames:v", "1",
        str(dest),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=60)
        return dest.exists() and dest.stat().st_size > 0
    except Exception:
        dest.unlink(missing_ok=True)
        return False


def convert_image_for_web(source: Path, *, thumb: bool = False) -> Path | None:
    """Convert HEIC/TIFF/images to browser-friendly JPEG, cached on disk."""
    max_size = _THUMB_MAX if thumb else _FULL_MAX
    cached = _cache_key(source, f"img:{max_size}")
    if cached.exists():
        return cached
    if _convert_image_ffmpeg_gpu(source, cached, max_size):
        return cached
    if _convert_image_pillow(source, cached, max_size):
        return cached
    return None


def media_thumbnail(source: Path) -> Path | None:
    """Small cached JPEG for gallery grids (photos, HEIC, or video poster)."""
    suffix = source.suffix.lower()
    if suffix in {".heic", ".heif", ".tif", ".tiff", ".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        return convert_image_for_web(source, thumb=True)
    if suffix in {".mp4", ".mov", ".m4v", ".webm"}:
        cached = _cache_key(source, f"vidthumb:{_THUMB_MAX}")
        if cached.exists():
            return cached
        import subprocess

        cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
        if "cuda" in _ffmpeg_hwaccel_list():
            cmd += ["-hwaccel", "cuda"]
        cmd += ["-ss", "0.5", "-i", str(source), "-frames:v", "1", "-vf", f"scale={_THUMB_MAX}:-2", str(cached)]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=90)
            return cached if cached.exists() else None
        except Exception:
            cached.unlink(missing_ok=True)
            return None
    return convert_image_for_web(source, thumb=True)


def serve_media_file(resolved: Path, *, thumb: bool = False) -> tuple[Path, str]:
    """Return (path, media_type) for a resolved attachment file."""
    suffix = resolved.suffix.lower()
    if thumb:
        preview = media_thumbnail(resolved)
        if preview:
            return preview, "image/jpeg"
    if suffix in {".heic", ".heif", ".tif", ".tiff"}:
        converted = convert_image_for_web(resolved)
        if converted:
            return converted, "image/jpeg"
    if suffix == ".caf":
        converted = convert_audio_for_web(resolved)
        if converted:
            return converted, "audio/mp4"
    import mimetypes

    media_type, _ = mimetypes.guess_type(str(resolved))
    return resolved, media_type or "application/octet-stream"


def gpu_status() -> dict[str, Any]:
    providers: list[str] = []
    try:
        from app.indexer import indexer

        providers = list(indexer.embedder.model.model.get_providers())
    except Exception:
        pass
    return {
        "search_on_gpu": "CUDAExecutionProvider" in providers,
        "embed_providers": providers,
        "media_ffmpeg_cuda": gpu_media_available(),
        "ffmpeg_hwaccels": _ffmpeg_hwaccel_list(),
    }


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
    """Resolve a stored relative path to an on-disk file. Never guesses by filename alone."""
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
    return None


@lru_cache(maxsize=2)
def _attachment_paths_cached(mtime: float) -> dict[int, list[str]]:
    """Map attachment_id -> ordered candidate paths (from JSONL export)."""
    index: dict[int, list[str]] = {}
    for msg in _messages_cached(mtime):
        for att in msg.get("attachments") or []:
            att_id = att.get("attachment_id")
            if att_id is None:
                continue
            paths = att.get("paths") or ([att["path"]] if att.get("path") else [])
            # Later messages win for the same id (shouldn't happen, but be safe).
            index[int(att_id)] = paths
    return index


def resolve_media_by_attachment_id(attachment_id: int) -> Path | None:
    """Resolve media using the unique attachment ROWID — avoids filename collisions."""
    paths = _attachment_paths_cached(_jsonl_mtime()).get(attachment_id, [])
    for rel in paths:
        resolved = resolve_media_path(rel)
        if resolved:
            return resolved
    # Html-export files are named {attachment_id}.{ext}
    if HTML_ATTACHMENTS_DIR.exists():
        for path in HTML_ATTACHMENTS_DIR.rglob(f"{attachment_id}.*"):
            if path.is_file():
                return path
    return None


def media_gallery(kind: str = "all", limit: int = 200, offset: int = 0) -> dict[str, Any]:
    """All media attachments across chats, newest first."""
    items = _media_gallery_cached(_jsonl_mtime(), kind)
    total = len(items)
    return {"total": total, "items": items[offset : offset + limit]}


@lru_cache(maxsize=4)
def _media_gallery_cached(mtime: float, kind: str) -> tuple[dict[str, Any], ...]:
    items: list[dict[str, Any]] = []
    for msg in _messages_cached(mtime):
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
                "attachment_id": att.get("attachment_id"),
                "chat": msg.get("chat"),
                "chat_id": msg.get("chat_id"),
                "sender": msg.get("sender"),
                "date": msg.get("date"),
            })
    items.sort(key=lambda i: i.get("date") or "", reverse=True)
    return tuple(items)


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
    _attachment_paths_cached.cache_clear()
    _media_gallery_cached.cache_clear()
