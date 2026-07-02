#!/usr/bin/env python3
"""Export iMessage chat.db to JSONL with contacts and attachment metadata."""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


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


def normalize_phone(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    if len(digits) == 11 and digits.startswith("1"):
        return digits[1:]
    return digits


def load_contacts(path: Path | None) -> dict[str, str]:
    if not path or not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_contact(handle: str, contacts: dict[str, str]) -> str:
    if not handle or handle == "Me":
        return handle
    if handle in contacts:
        return contacts[handle]
    norm = normalize_phone(handle)
    if norm in contacts:
        return contacts[norm]
    if f"+1{norm}" in contacts:
        return contacts[f"+1{norm}"]
    email = handle.strip().lower()
    if email in contacts:
        return contacts[email]
    return handle


def build_html_index(html_dir: Path) -> dict[int, str]:
    """Map attachment ROWID -> html-export relative path."""
    index: dict[int, str] = {}
    att_root = html_dir / "attachments"
    if not att_root.exists():
        return index
    for path in att_root.rglob("*"):
        if not path.is_file():
            continue
        try:
            rowid = int(path.stem.split(".")[0]) if path.stem.split(".")[0].isdigit() else int(path.name.split(".")[0])
        except ValueError:
            continue
        rel = Path("html-export") / "attachments" / path.relative_to(att_root)
        index[rowid] = str(rel).replace("\\", "/")
    return index


def resolve_chat_names(conn: sqlite3.Connection, chat_id: int, contacts: dict[str, str]) -> list[str]:
    rows = conn.execute(
        "SELECT h.id FROM chat_handle_join chj JOIN handle h ON h.ROWID = chj.handle_id WHERE chj.chat_id = ? ORDER BY h.id",
        (chat_id,),
    ).fetchall()
    return [resolve_contact(r[0], contacts) for r in rows]


def _file_relative(messages_root: Path, file_path: Path) -> str | None:
    try:
        rel = file_path.relative_to(messages_root)
        return f"raw/{rel}".replace("\\", "/")
    except ValueError:
        return None


def message_attachments(
    conn: sqlite3.Connection,
    message_id: int,
    messages_root: Path,
    html_index: dict[int, str],
) -> list[dict]:
    rows = conn.execute(
        """
        SELECT a.ROWID, a.filename, a.transfer_name, a.mime_type, a.uti
        FROM attachment a
        JOIN message_attachment_join maj ON maj.attachment_id = a.ROWID
        WHERE maj.message_id = ?
        """,
        (message_id,),
    ).fetchall()
    items = []
    for row in rows:
        att_id, filename, transfer, mime, uti = row
        transfer = transfer or (Path(filename).name if filename else f"attachment-{att_id}")
        mime = mime or _guess_mime(transfer, uti)
        paths: list[str] = []

        if att_id in html_index:
            paths.append(html_index[att_id])

        if filename:
            base = Path(filename.replace("~", str(Path.home())))
            files = [base] if base.is_file() else list(base.rglob("*")) if base.is_dir() else []
            for fpath in files:
                if not fpath.is_file():
                    continue
                rel = _file_relative(messages_root, fpath)
                if rel and rel not in paths:
                    paths.append(rel)

        if not paths:
            continue
        items.append({
            "name": transfer,
            "mime_type": mime,
            "path": paths[0],
            "paths": paths,
            "attachment_id": att_id,
        })
    return items


def _guess_mime(name: str, uti: str | None) -> str:
    ext = Path(name).suffix.lower()
    mapping = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".gif": "image/gif",
        ".heic": "image/heic", ".mp4": "video/mp4", ".mov": "video/quicktime",
        ".m4a": "audio/mp4", ".caf": "audio/x-caf", ".mp3": "audio/mpeg",
    }
    if ext in mapping:
        return mapping[ext]
    if uti and "image" in uti:
        return "image/jpeg"
    if uti and "movie" in uti:
        return "video/mp4"
    return "application/octet-stream"


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
    contacts = load_contacts(Path(args.contacts).expanduser()) if args.contacts else {}
    html_index = build_html_index(Path(args.html_dir).expanduser()) if args.html_dir else {}

    if not db_path.exists():
        print(f"ERROR: database not found: {db_path}", file=sys.stderr)
        return 1

    out_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    query = """
        SELECT m.ROWID AS message_id, m.text, m.is_from_me, m.date, m.service,
               m.cache_has_attachments, c.ROWID AS chat_id, c.chat_identifier, c.display_name
        FROM message m
        JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
        JOIN chat c ON c.ROWID = cmj.chat_id
        WHERE (m.text IS NOT NULL AND m.text != '') OR m.cache_has_attachments = 1
        ORDER BY m.date ASC
    """

    count = 0
    with out_path.open("w", encoding="utf-8") as out:
        for row in conn.execute(query):
            text = str(row["text"]).strip() if row["text"] else ""
            attachments = message_attachments(conn, row["message_id"], messages_root, html_index)
            if not text and not attachments:
                continue

            raw_participants = conn.execute(
                "SELECT h.id FROM chat_handle_join chj JOIN handle h ON h.ROWID = chj.handle_id WHERE chj.chat_id = ?",
                (row["chat_id"],),
            ).fetchall()
            raw_handles = [r[0] for r in raw_participants]
            participants = [resolve_contact(h, contacts) for h in raw_handles]

            if row["display_name"]:
                chat_label = row["display_name"]
            elif len(participants) == 1:
                chat_label = participants[0]
            elif len(participants) > 1:
                chat_label = ", ".join(participants[:3]) + ("..." if len(participants) > 3 else "")
            else:
                chat_label = resolve_contact(row["chat_identifier"] or "", contacts)

            sender = "Me" if row["is_from_me"] else (participants[0] if len(participants) == 1 else "Them")

            record = {
                "id": f"{row['chat_id']}:{row['message_id']}",
                "message_id": row["message_id"],
                "chat_id": row["chat_id"],
                "chat": chat_label,
                "participants": participants,
                "participant_handles": raw_handles,
                "sender": sender,
                "is_from_me": bool(row["is_from_me"]),
                "service": row["service"],
                "date": apple_time_to_iso(row["date"]),
                "has_attachments": bool(attachments),
                "text": text,
                "attachments": attachments,
            }
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
            count += 1

    conn.close()
    print(f"Exported {count} messages ({len(contacts)} contacts, {len(html_index)} html attachments) to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
