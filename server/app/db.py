from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from typing import Any

from app.config import DB_PATH, STATE_DIR

SCHEMA = """
CREATE TABLE IF NOT EXISTS clients (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    hostname TEXT NOT NULL,
    token TEXT NOT NULL,
    created_at REAL NOT NULL,
    last_seen_at REAL
);

CREATE TABLE IF NOT EXISTS schedules (
    client_id TEXT PRIMARY KEY REFERENCES clients(id) ON DELETE CASCADE,
    enabled INTEGER NOT NULL DEFAULT 0,
    days_json TEXT NOT NULL DEFAULT '[]',
    hour INTEGER NOT NULL DEFAULT 3,
    minute INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS backup_runs (
    id TEXT PRIMARY KEY,
    client_id TEXT NOT NULL REFERENCES clients(id),
    status TEXT NOT NULL,
    phase TEXT,
    message TEXT,
    started_at REAL NOT NULL,
    finished_at REAL,
    triggered_by TEXT NOT NULL DEFAULT 'schedule'
);

CREATE TABLE IF NOT EXISTS pending_triggers (
    client_id TEXT PRIMARY KEY REFERENCES clients(id) ON DELETE CASCADE,
    created_at REAL NOT NULL
);
"""


def init_db() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.executescript(SCHEMA)


@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def register_client(name: str, hostname: str) -> dict[str, Any]:
    now = time.time()
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM clients WHERE hostname = ?", (hostname,)
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE clients SET name = ?, last_seen_at = ? WHERE id = ?",
                (name, now, row["id"]),
            )
            return dict(row) | {"name": name, "last_seen_at": now}

        client_id = str(uuid.uuid4())
        token = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO clients (id, name, hostname, token, created_at, last_seen_at) VALUES (?, ?, ?, ?, ?, ?)",
            (client_id, name, hostname, token, now, now),
        )
        conn.execute(
            "INSERT INTO schedules (client_id, enabled, days_json, hour, minute, updated_at) VALUES (?, 0, '[]', 3, 0, ?)",
            (client_id, now),
        )
        return {
            "id": client_id,
            "name": name,
            "hostname": hostname,
            "token": token,
            "created_at": now,
            "last_seen_at": now,
        }


def get_client_by_token(token: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM clients WHERE token = ?", (token,)).fetchone()
        return dict(row) if row else None


def list_clients() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT c.*, s.enabled, s.days_json, s.hour, s.minute,
                   (SELECT status FROM backup_runs WHERE client_id = c.id ORDER BY started_at DESC LIMIT 1) AS last_status,
                   (SELECT started_at FROM backup_runs WHERE client_id = c.id ORDER BY started_at DESC LIMIT 1) AS last_backup_at,
                   (SELECT finished_at FROM backup_runs WHERE client_id = c.id ORDER BY started_at DESC LIMIT 1) AS last_finished_at,
                   EXISTS(SELECT 1 FROM pending_triggers WHERE client_id = c.id) AS trigger_pending
            FROM clients c
            LEFT JOIN schedules s ON s.client_id = c.id
            ORDER BY c.name
            """
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["days"] = json.loads(item.pop("days_json") or "[]")
            item["schedule_enabled"] = bool(item.pop("enabled"))
            item["trigger_pending"] = bool(item["trigger_pending"])
            result.append(item)
        return result


def update_schedule(client_id: str, enabled: bool, days: list[int], hour: int, minute: int) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO schedules (client_id, enabled, days_json, hour, minute, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(client_id) DO UPDATE SET
                enabled = excluded.enabled,
                days_json = excluded.days_json,
                hour = excluded.hour,
                minute = excluded.minute,
                updated_at = excluded.updated_at
            """,
            (client_id, int(enabled), json.dumps(days), hour, minute, time.time()),
        )


def queue_trigger(client_id: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO pending_triggers (client_id, created_at) VALUES (?, ?)",
            (client_id, time.time()),
        )


def pop_trigger(client_id: str) -> bool:
    with connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM pending_triggers WHERE client_id = ?", (client_id,)
        ).fetchone()
        if row:
            conn.execute("DELETE FROM pending_triggers WHERE client_id = ?", (client_id,))
            return True
        return False


def get_schedule(client_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM schedules WHERE client_id = ?", (client_id,)).fetchone()
        if not row:
            return None
        return {
            "enabled": bool(row["enabled"]),
            "days": json.loads(row["days_json"] or "[]"),
            "hour": row["hour"],
            "minute": row["minute"],
        }


def create_backup_run(client_id: str, triggered_by: str) -> str:
    run_id = str(uuid.uuid4())
    now = time.time()
    with connect() as conn:
        conn.execute(
            "INSERT INTO backup_runs (id, client_id, status, phase, message, started_at, triggered_by) VALUES (?, ?, 'running', 'starting', '', ?, ?)",
            (run_id, client_id, now, triggered_by),
        )
    return run_id


def update_backup_run(run_id: str, status: str | None = None, phase: str | None = None, message: str | None = None) -> None:
    with connect() as conn:
        fields = []
        values: list[Any] = []
        if status is not None:
            fields.append("status = ?")
            values.append(status)
        if phase is not None:
            fields.append("phase = ?")
            values.append(phase)
        if message is not None:
            fields.append("message = ?")
            values.append(message)
        if status in ("success", "error"):
            fields.append("finished_at = ?")
            values.append(time.time())
        if not fields:
            return
        values.append(run_id)
        conn.execute(f"UPDATE backup_runs SET {', '.join(fields)} WHERE id = ?", values)


def list_backup_runs(limit: int = 50) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT r.*, c.name AS client_name
            FROM backup_runs r
            JOIN clients c ON c.id = r.client_id
            ORDER BY r.started_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def touch_client(client_id: str) -> None:
    with connect() as conn:
        conn.execute("UPDATE clients SET last_seen_at = ? WHERE id = ?", (time.time(), client_id))
