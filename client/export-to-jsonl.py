#!/usr/bin/env python3
"""Export iMessage chat.db to JSONL with attachment metadata."""
from __future__ import annotations

import argparse
import json
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
    epoch = 978307200
    return datetime.fromtimestamp(epoch + seconds, tz=timezone.utc).isoformat()


def resolve_chat_names(conn: sqlite3.Connection, chat_id: int) -> list[str]:
    rows = conn.execute(
        """
        SELECT h.id FROM chat_handle_join chj
        JOIN handle h ON h.ROWID = chj.handle_id
        WHERE chj.chat_id = ? ORDER BY h.id
        """,
        (chat_id,),
    ).fetchall()
    return [r[0] for r in rows]


def message_attachments(conn: sqlite3.Connection, message_id: int, messages_root: Path) -> list[dict]:
    rows = conn.execute(
        """
        SELECT a.filename, a.transfer_name, a.mime_type
        FROM attachment a
        JOIN message_attachment_join maj ON maj.attachment_id = a.ROWID
        WHERE maj.message_id = ?
        """,
        (message_id,),
    ).fetchall()
    items = []
    for row in rows:
        filename = row[0] or ""
        transfer = row[1] or Path(filename).name
        mime = row[2] or ""
        rel = None
        if filename:
            p = Path(filename.replace("~", str(Path.home())))
            try:
                rel = str(p.relative_to(messages_root))
            except ValueError:
                if "Attachments/" in filename:
                    rel = filename.split("Attachments/", 1)[-1]
                    rel = f"Attachments/{rel}"
        if rel:
            items.append({
                "name": transfer,
                "mime_type": mime,
                "path": f"raw/{rel}" if not rel.startswith("raw/") else rel,
            })
    return items


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(Path.home() / "Library/Messages/chat.db"))
    parser.add_argument("--out", required=True)
    parser.add_argument("--messages-root", default=str(Path.home() / "Library/Messages"))
    args = parser.parse_args()

    db_path = Path(args.db).expanduser()
    out_path = Path(args.out).expanduser()
    messages_root = Path(args.messages_root).expanduser()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if not db_path.exists():
        print(f"ERROR: database not found: {db_path}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    query = """
        SELECT m.ROWID AS message_id, m.text, m.attributedBody, m.is_from_me, m.date,
               m.service, m.cache_has_attachments, c.ROWID AS chat_id,
               c.chat_identifier, c.display_name
        FROM message m
        JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
        JOIN chat c ON c.ROWID = cmj.chat_id
        WHERE (m.text IS NOT NULL AND m.text != '')
           OR m.attributedBody IS NOT NULL
           OR m.cache_has_attachments = 1
        ORDER BY m.date ASC
    """

    count = 0
    with out_path.open("w", encoding="utf-8") as out:
        for row in conn.execute(query):
            text = row["text"]
            if text:
                text = str(text).strip()
            attachments = message_attachments(conn, row["message_id"], messages_root)
            if not text and not attachments:
                continue

            participants = resolve_chat_names(conn, row["chat_id"])
            chat_label = row["display_name"] or row["chat_identifier"] or ", ".join(participants)
            sender = "Me" if row["is_from_me"] else (participants[0] if len(participants) == 1 else "Them")

            record = {
                "id": f"{row['chat_id']}:{row['message_id']}",
                "message_id": row["message_id"],
                "chat_id": row["chat_id"],
                "chat": chat_label,
                "participants": participants,
                "sender": sender,
                "is_from_me": bool(row["is_from_me"]),
                "service": row["service"],
                "date": apple_time_to_iso(row["date"]),
                "has_attachments": bool(attachments) or bool(row["cache_has_attachments"]),
                "text": text or "",
                "attachments": attachments,
            }
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
            count += 1

    conn.close()
    print(f"Exported {count} messages to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
