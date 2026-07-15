"""JSONL merge helper tests."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_merge_jsonl_upsert(tmp_path: Path) -> None:
    base = tmp_path / "base.jsonl"
    delta = tmp_path / "delta.jsonl"
    out = tmp_path / "out.jsonl"
    base.write_text(
        json.dumps({"id": "1:10", "message_id": 10, "date": "2020-01-01T00:00:00", "text": "old"}) + "\n",
        encoding="utf-8",
    )
    delta.write_text(
        "\n".join(
            [
                json.dumps({"id": "1:10", "message_id": 10, "date": "2020-01-01T00:00:00", "text": "updated"}),
                json.dumps({"id": "1:11", "message_id": 11, "date": "2020-01-02T00:00:00", "text": "new"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    script = Path(__file__).resolve().parents[2] / "client" / "merge-jsonl.py"
    proc = subprocess.run(
        [sys.executable, str(script), "--base", str(base), "--delta", str(delta), "--out", str(out)],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "total" in proc.stdout.lower() or "+" in proc.stdout
    rows = [json.loads(line) for line in out.read_text().splitlines() if line.strip()]
    assert len(rows) == 2
    by_id = {r["id"]: r for r in rows}
    assert by_id["1:10"]["text"] == "updated"
    assert by_id["1:11"]["text"] == "new"
    ids = (tmp_path / "out.new-ids.txt").read_text().splitlines()
    assert "1:10" in ids and "1:11" in ids
