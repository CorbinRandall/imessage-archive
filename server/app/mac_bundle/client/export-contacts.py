#!/usr/bin/env python3
"""Export macOS Contacts to JSON lookup for iMessage archive."""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path


def normalize_phone(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    if len(digits) == 11 and digits.startswith("1"):
        return digits[1:]
    return digits


def normalize_email(value: str) -> str:
    return (value or "").strip().lower()


def display_name(first: str | None, last: str | None, org: str | None = None) -> str:
    first = (first or "").strip()
    last = (last or "").strip()
    org = (org or "").strip()
    if first or last:
        return f"{first} {last}".strip()
    return org


def load_addressbook(db_path: Path, lookup: dict[str, str]) -> int:
    added = 0
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        records = conn.execute(
            "SELECT Z_PK, ZFIRSTNAME, ZLASTNAME, ZORGANIZATION FROM ZABCDRECORD"
        ).fetchall()
        pk_to_name = {
            pk: display_name(first, last, org)
            for pk, first, last, org in records
        }

        for pk, number in conn.execute(
            "SELECT ZOWNER, ZFULLNUMBER FROM ZABCDPHONENUMBER WHERE ZFULLNUMBER IS NOT NULL"
        ):
            name = pk_to_name.get(pk)
            if not name:
                continue
            norm = normalize_phone(number)
            if norm and norm not in lookup:
                lookup[norm] = name
                lookup[f"+1{norm}"] = name
                added += 1

        for pk, email in conn.execute(
            "SELECT ZOWNER, ZADDRESS FROM ZABCDEMAILADDRESS WHERE ZADDRESS IS NOT NULL"
        ):
            name = pk_to_name.get(pk)
            if not name:
                continue
            norm = normalize_email(email)
            if norm and norm not in lookup:
                lookup[norm] = name
                added += 1
    finally:
        conn.close()
    return added


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, help="Output contacts.json path")
    parser.add_argument(
        "--addressbook-root",
        default=str(Path.home() / "Library/Application Support/AddressBook"),
    )
    args = parser.parse_args()

    root = Path(args.addressbook_root).expanduser()
    out_path = Path(args.out).expanduser()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    lookup: dict[str, str] = {}
    db_files = [root / "AddressBook-v22.abcddb"]
    db_files.extend(root.glob("Sources/*/AddressBook-v22.abcddb"))

    found = 0
    for db_path in db_files:
        if not db_path.exists():
            continue
        try:
            found += load_addressbook(db_path, lookup)
        except sqlite3.Error as exc:
            print(f"WARN: {db_path}: {exc}", file=sys.stderr)

    out_path.write_text(json.dumps(lookup, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Exported {len(lookup)} contact keys from {len(db_files)} sources to {out_path}")
    return 0 if lookup else (0 if found == 0 else 0)


if __name__ == "__main__":
    raise SystemExit(main())
