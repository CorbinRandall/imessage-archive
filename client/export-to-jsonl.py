#!/usr/bin/env python3
"""Export iMessage chat.db to JSONL at full fidelity.

Includes: per-message senders (group chats), tapback reactions attached to
their target messages, attributedBody text decoding, threads, and media
attachment metadata. Filters out plugin-payload junk.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

TAPBACK_EMOJI = {
    2000: "\u2764\ufe0f",   # loved
    2001: "\U0001f44d",     # liked
    2002: "\U0001f44e",     # disliked
    2003: "\U0001f602",     # laughed
    2004: "\u203c\ufe0f",   # emphasized
    2005: "\u2753",         # questioned
}

JUNK_ATTACHMENT_SUFFIXES = (".pluginpayloadattachment",)
JUNK_UTI_MARKERS = ("dyn.age81a5dzq7y",)


def apple_time_to_iso(raw: int | None) -> str | None:
    if raw is None:
        return None
    if raw > 1_000_000_000_000_000:
        seconds = raw / 1_000_000_000
    elif raw > 1_000_000_000_000:
        seconds = raw / 1_000_000
    else:
        seconds = float(raw)
    return datetime.fromtimestamp(978307200 + seconds, tz=timezone.utc).isoformat()


def decode_attributed_body(data: bytes | None) -> str | None:
    """Extract plain text from a typedstream attributedBody blob."""
    if not data:
        return None
    idx = data.find(b"NSString")
    if idx == -1:
        return None
    idx = data.find(b"+", idx)
    if idx == -1:
        return None
    idx += 1
    if idx >= len(data):
        return None
    length = data[idx]
    idx += 1
    if length == 0x81:
        if idx + 2 > len(data):
            return None
        length = int.from_bytes(data[idx : idx + 2], "little")
        idx += 2
    elif length == 0x82:
        if idx + 4 > len(data):
            return None
        length = int.from_bytes(data[idx : idx + 4], "little")
        idx += 4
    text = data[idx : idx + length].decode("utf-8", errors="ignore")
    # Strip object-replacement chars left by inline attachments
    return text.replace("\ufffc", "").strip() or None


def normalize_phone(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    if len(digits) == 11 and digits.startswith("1"):
        return digits[1:]
    return digits


def load_contacts(path: Path | None) -> dict[str, str]:
    if not path or not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


class ContactResolver:
    def __init__(self, contacts: dict[str, str]):
        self.raw = contacts
        self.by_phone: dict[str, str] = {}
        for key, name in contacts.items():
            digits = normalize_phone(key)
            if len(digits) >= 10:
                self.by_phone[digits[-10:]] = name

    def resolve(self, handle: str | None) -> str:
        if not handle:
            return "Unknown"
        if handle in self.raw:
            return self.raw[handle]
        digits = normalize_phone(handle)
        if len(digits) >= 10 and digits[-10:] in self.by_phone:
            return self.by_phone[digits[-10:]]
        email = handle.strip().lower()
        if email in self.raw:
            return self.raw[email]
        return handle


def is_junk_attachment(transfer_name: str, uti: str | None, mime: str | None) -> bool:
    name = (transfer_name or "").lower()
    if any(name.endswith(sfx) for sfx in JUNK_ATTACHMENT_SUFFIXES):
        return True
    if uti and any(marker in uti for marker in JUNK_UTI_MARKERS):
        return True
    return False


def guess_mime(name: str, uti: str | None) -> str:
    ext = Path(name).suffix.lower()
    mapping = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
        ".gif": "image/gif", ".heic": "image/heic", ".webp": "image/webp",
        ".tiff": "image/tiff", ".bmp": "image/bmp",
        ".mp4": "video/mp4", ".mov": "video/quicktime", ".m4v": "video/mp4",
        ".m4a": "audio/mp4", ".caf": "audio/x-caf", ".mp3": "audio/mpeg",
        ".aac": "audio/aac", ".wav": "audio/wav",
        ".pdf": "application/pdf", ".vcf": "text/vcard",
    }
    if ext in mapping:
        return mapping[ext]
    uti = uti or ""
    if "jpeg" in uti or "image" in uti:
        return "image/jpeg"
    if "movie" in uti or "video" in uti or "quicktime" in uti:
        return "video/mp4"
    if "audio" in uti:
        return "audio/mp4"
    return "application/octet-stream"


def build_html_index(html_dir: Path) -> dict[int, str]:
    """Map attachment ROWID -> html-export relative path (web-friendly copies)."""
    index: dict[int, str] = {}
    att_root = html_dir / "attachments"
    if not att_root.exists():
        return index
    for path in att_root.rglob("*"):
        if not path.is_file():
            continue
        stem = path.name.split(".")[0]
        if not stem.isdigit():
            continue
        rel = Path("html-export") / "attachments" / path.relative_to(att_root)
        index[int(stem)] = str(rel).replace("\\", "/")
    return index


def load_all_attachments(conn: sqlite3.Connection, messages_root: Path, html_index: dict[int, str]) -> dict[int, list[dict]]:
    """Preload every attachment keyed by message_id."""
    by_message: dict[int, list[dict]] = {}
    rows = conn.execute(
        """
        SELECT maj.message_id, a.ROWID, a.filename, a.transfer_name, a.mime_type, a.uti
        FROM attachment a
        JOIN message_attachment_join maj ON maj.attachment_id = a.ROWID
        """
    ).fetchall()
    home = str(Path.home())
    for message_id, att_id, filename, transfer, mime, uti in rows:
        transfer = transfer or (Path(filename).name if filename else f"attachment-{att_id}")
        if is_junk_attachment(transfer, uti, mime):
            continue
        mime = mime or guess_mime(transfer, uti)
        paths: list[str] = []
        if att_id in html_index:
            paths.append(html_index[att_id])
        if filename:
            base = Path(filename.replace("~", home))
            candidates = [base] if base.is_file() else (list(base.rglob("*")) if base.is_dir() else [])
            for fpath in candidates:
                if not fpath.is_file():
                    continue
                try:
                    rel = fpath.relative_to(messages_root)
                    p = f"raw/{rel}".replace("\\", "/")
                    if p not in paths:
                        paths.append(p)
                except ValueError:
                    pass
        if not paths:
            continue
        # Prefer html-export (attachment-id filenames) over raw GUID paths — both are unique.
        paths.sort(key=lambda p: (0 if p.startswith("html-export/") else 1, p))
        by_message.setdefault(message_id, []).append({
            "name": transfer,
            "mime_type": mime,
            "path": paths[0],
            "paths": paths,
            "attachment_id": att_id,
        })
    return by_message


def load_reactions(conn: sqlite3.Connection, resolver: ContactResolver) -> dict[str, list[dict]]:
    """Collect tapbacks keyed by target message guid. Applies removals."""
    rows = conn.execute(
        """
        SELECT m.associated_message_guid, m.associated_message_type,
               m.associated_message_emoji, m.is_from_me, h.id AS handle, m.date
        FROM message m
        LEFT JOIN handle h ON h.ROWID = m.handle_id
        WHERE m.associated_message_type BETWEEN 2000 AND 3999
          AND m.associated_message_guid IS NOT NULL
        ORDER BY m.date ASC
        """
    ).fetchall()

    # key: (target_guid, sender, base_type) -> reaction dict; removals (3xxx) delete
    active: dict[tuple, dict] = {}
    for assoc_guid, assoc_type, emoji, is_from_me, handle, date in rows:
        target_guid = assoc_guid.split("/")[-1] if "/" in assoc_guid else assoc_guid.removeprefix("bp:")
        sender = "Me" if is_from_me else resolver.resolve(handle)
        base_type = assoc_type % 1000  # 2000->0 ... 2005->5, 3000->0 ...
        key = (target_guid, sender, base_type)
        if 2000 <= assoc_type <= 2999:
            display = emoji or TAPBACK_EMOJI.get(2000 + base_type, "\u2764\ufe0f")
            active[key] = {"emoji": display, "sender": sender}
        else:  # 3000-3999 removal
            active.pop(key, None)

    result: dict[str, list[dict]] = {}
    for (target_guid, _sender, _bt), reaction in active.items():
        result.setdefault(target_guid, []).append(reaction)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(Path.home() / "Library/Messages/chat.db"))
    parser.add_argument("--out", required=True)
    parser.add_argument("--messages-root", default=str(Path.home() / "Library/Messages"))
    parser.add_argument("--contacts", default="")
    parser.add_argument("--html-dir", default="")
    args = parser.parse_args()

    db_path = Path(args.db).expanduser()
    out_path = Path(args.out).expanduser()
    messages_root = Path(args.messages_root).expanduser()
    resolver = ContactResolver(load_contacts(Path(args.contacts).expanduser()) if args.contacts else {})
    html_index = build_html_index(Path(args.html_dir).expanduser()) if args.html_dir else {}

    if not db_path.exists():
        print(f"ERROR: database not found: {db_path}", file=sys.stderr)
        return 1

    out_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    print("Loading attachments...")
    attachments_by_msg = load_all_attachments(conn, messages_root, html_index)
    print("Loading reactions...")
    reactions_by_guid = load_reactions(conn, resolver)

    # Chat participants (for chat labels)
    chat_participants: dict[int, list[str]] = {}
    for chat_id, handle in conn.execute(
        "SELECT chj.chat_id, h.id FROM chat_handle_join chj JOIN handle h ON h.ROWID = chj.handle_id ORDER BY h.id"
    ):
        chat_participants.setdefault(chat_id, []).append(handle)

    query = """
        SELECT m.ROWID AS message_id, m.guid, m.text, m.attributedBody, m.is_from_me,
               m.date, m.service, m.associated_message_type, m.balloon_bundle_id,
               m.thread_originator_guid, m.date_edited, m.date_retracted,
               h.id AS sender_handle,
               c.ROWID AS chat_id, c.chat_identifier, c.display_name, c.style
        FROM message m
        JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
        JOIN chat c ON c.ROWID = cmj.chat_id
        LEFT JOIN handle h ON h.ROWID = m.handle_id
        WHERE m.associated_message_type NOT BETWEEN 2000 AND 3999
        ORDER BY m.date ASC
    """

    count = 0
    skipped = 0
    with out_path.open("w", encoding="utf-8") as out:
        for row in conn.execute(query):
            text = str(row["text"]).strip() if row["text"] else ""
            if not text:
                text = decode_attributed_body(row["attributedBody"]) or ""

            atts = attachments_by_msg.get(row["message_id"], [])
            if not text and not atts:
                skipped += 1
                continue

            raw_handles = chat_participants.get(row["chat_id"], [])
            participants = [resolver.resolve(h) for h in raw_handles]
            is_group = bool(row["style"] == 43 or len(raw_handles) > 1)

            if row["is_from_me"]:
                sender = "Me"
            elif row["sender_handle"]:
                sender = resolver.resolve(row["sender_handle"])
            elif len(participants) == 1:
                sender = participants[0]
            else:
                sender = "Unknown"

            if row["display_name"]:
                chat_label = row["display_name"]
            elif len(participants) == 1:
                chat_label = participants[0]
            elif participants:
                chat_label = ", ".join(participants[:3]) + ("..." if len(participants) > 3 else "")
            else:
                chat_label = resolver.resolve(row["chat_identifier"] or "")

            record = {
                "id": f"{row['chat_id']}:{row['message_id']}",
                "message_id": row["message_id"],
                "guid": row["guid"],
                "chat_id": row["chat_id"],
                "chat": chat_label,
                "is_group": is_group,
                "participants": participants,
                "participant_handles": raw_handles,
                "sender": sender,
                "sender_handle": row["sender_handle"],
                "is_from_me": bool(row["is_from_me"]),
                "service": row["service"],
                "date": apple_time_to_iso(row["date"]),
                "edited": bool(row["date_edited"]),
                "retracted": bool(row["date_retracted"]),
                "thread_originator_guid": row["thread_originator_guid"],
                "has_attachments": bool(atts),
                "text": text,
                "attachments": atts,
                "reactions": reactions_by_guid.get(row["guid"], []),
                "app_message": bool(row["balloon_bundle_id"]),
            }
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
            count += 1

    conn.close()
    print(
        f"Exported {count} messages ({skipped} empty skipped, "
        f"{len(reactions_by_guid)} messages with reactions, "
        f"{len(resolver.raw)} contacts, {len(html_index)} html attachments) to {out_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
