#!/usr/bin/env python3
"""Upsert delta messages into a base messages.jsonl by record id."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def load_jsonl(path: Path) -> dict[str, dict]:
    by_id: dict[str, dict] = {}
    if not path.exists():
        return by_id
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            msg = json.loads(line)
            key = str(msg.get("id") or f"{msg.get('chat_id')}:{msg.get('message_id')}")
            by_id[key] = msg
    return by_id


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="Existing messages.jsonl (may be missing)")
    parser.add_argument("--delta", required=True, help="New/updated records to upsert")
    parser.add_argument("--out", required=True, help="Output merged JSONL")
    args = parser.parse_args()

    base_path = Path(args.base).expanduser()
    delta_path = Path(args.delta).expanduser()
    out_path = Path(args.out).expanduser()

    merged = load_jsonl(base_path)
    before = len(merged)
    added = 0
    updated = 0
    with delta_path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            msg = json.loads(line)
            key = str(msg.get("id") or f"{msg.get('chat_id')}:{msg.get('message_id')}")
            if key in merged:
                updated += 1
            else:
                added += 1
            merged[key] = msg

    # Stable chronological order by date then message_id
    records = sorted(
        merged.values(),
        key=lambda m: (m.get("date") or "", int(m.get("message_id") or 0)),
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as out:
        for msg in records:
            out.write(json.dumps(msg, ensure_ascii=False) + "\n")

    print(
        f"Merged JSONL: {before} base → {len(records)} total "
        f"(+{added} new, ~{updated} updated) → {out_path}"
    )
    # Emit new ids for incremental index (one per line to stderr JSON summary on stdout already)
    new_ids = []
    if delta_path.exists():
        with delta_path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                msg = json.loads(line)
                new_ids.append(str(msg.get("id") or f"{msg.get('chat_id')}:{msg.get('message_id')}"))
    ids_path = out_path.with_suffix(".new-ids.txt")
    ids_path.write_text("\n".join(new_ids) + ("\n" if new_ids else ""), encoding="utf-8")
    print(f"Wrote {len(new_ids)} new/updated ids → {ids_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
