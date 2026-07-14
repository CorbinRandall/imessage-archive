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
    id TEXT PRIMARY KEY,
    client_id TEXT NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    days_json TEXT NOT NULL DEFAULT '[]',
    hour INTEGER NOT NULL DEFAULT 3,
    minute INTEGER NOT NULL DEFAULT 0,
    last_run_at REAL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS backup_runs (
    id TEXT PRIMARY KEY,
    client_id TEXT NOT NULL REFERENCES clients(id),
    schedule_id TEXT,
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

CREATE TABLE IF NOT EXISTS pending_cancels (
    client_id TEXT PRIMARY KEY REFERENCES clients(id) ON DELETE CASCADE,
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_schedules_client ON schedules(client_id);
"""


def init_db() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.executescript(SCHEMA)
        _migrate_legacy_schedule(conn)


def _migrate_legacy_schedule(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(schedules)")}
    if cols and "name" not in cols:
        rows = conn.execute(
            "SELECT client_id, enabled, days_json, hour, minute, updated_at FROM schedules"
        ).fetchall()
        conn.execute("ALTER TABLE schedules RENAME TO schedules_legacy")
        conn.executescript(
            """
            CREATE TABLE schedules (
                id TEXT PRIMARY KEY,
                client_id TEXT NOT NULL,
                name TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                days_json TEXT NOT NULL DEFAULT '[]',
                hour INTEGER NOT NULL DEFAULT 3,
                minute INTEGER NOT NULL DEFAULT 0,
                last_run_at REAL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_schedules_client ON schedules(client_id);
            """
        )
        now = time.time()
        for row in rows:
            conn.execute(
                """
                INSERT INTO schedules (id, client_id, name, enabled, days_json, hour, minute, created_at, updated_at)
                VALUES (?, ?, 'Default', ?, ?, ?, ?, ?, ?)
                """,
                (str(uuid.uuid4()), row[0], row[1], row[2], row[3], row[4], row[5] or now, row[5] or now),
            )
        conn.execute("DROP TABLE schedules_legacy")

    run_cols = {row[1] for row in conn.execute("PRAGMA table_info(backup_runs)")}
    if run_cols and "schedule_id" not in run_cols:
        conn.execute("ALTER TABLE backup_runs ADD COLUMN schedule_id TEXT")


@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _schedule_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "client_id": row["client_id"],
        "name": row["name"],
        "enabled": bool(row["enabled"]),
        "days": json.loads(row["days_json"] or "[]"),
        "hour": row["hour"],
        "minute": row["minute"],
        "last_run_at": row["last_run_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def register_client(name: str, hostname: str) -> dict[str, Any]:
    now = time.time()
    with connect() as conn:
        row = conn.execute("SELECT * FROM clients WHERE hostname = ?", (hostname,)).fetchone()
        if row:
            conn.execute("UPDATE clients SET name = ?, last_seen_at = ? WHERE id = ?", (name, now, row["id"]))
            return dict(row) | {"name": name, "last_seen_at": now}

        client_id = str(uuid.uuid4())
        token = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO clients (id, name, hostname, token, created_at, last_seen_at) VALUES (?, ?, ?, ?, ?, ?)",
            (client_id, name, hostname, token, now, now),
        )
        return {"id": client_id, "name": name, "hostname": hostname, "token": token, "created_at": now, "last_seen_at": now}


def get_client_by_token(token: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM clients WHERE token = ?", (token,)).fetchone()
        return dict(row) if row else None


def list_clients() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT c.*,
                   (SELECT status FROM backup_runs WHERE client_id = c.id ORDER BY started_at DESC LIMIT 1) AS last_status,
                   (SELECT started_at FROM backup_runs WHERE client_id = c.id ORDER BY started_at DESC LIMIT 1) AS last_backup_at,
                   (SELECT finished_at FROM backup_runs WHERE client_id = c.id ORDER BY started_at DESC LIMIT 1) AS last_finished_at,
                   EXISTS(SELECT 1 FROM pending_triggers WHERE client_id = c.id) AS trigger_pending
            FROM clients c ORDER BY c.name
            """
        ).fetchall()
        return [{**dict(r), "trigger_pending": bool(r["trigger_pending"])} for r in rows]


def list_schedules(client_id: str | None = None) -> list[dict[str, Any]]:
    with connect() as conn:
        if client_id:
            rows = conn.execute(
                """
                SELECT s.*, c.name AS client_name, c.hostname
                FROM schedules s JOIN clients c ON c.id = s.client_id
                WHERE s.client_id = ? ORDER BY s.name
                """,
                (client_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT s.*, c.name AS client_name, c.hostname
                FROM schedules s JOIN clients c ON c.id = s.client_id
                ORDER BY c.name, s.name
                """
            ).fetchall()
        return [{**_schedule_row(r), "client_name": r["client_name"], "hostname": r["hostname"]} for r in rows]


def get_schedule(schedule_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT s.*, c.name AS client_name, c.hostname
            FROM schedules s JOIN clients c ON c.id = s.client_id
            WHERE s.id = ?
            """,
            (schedule_id,),
        ).fetchone()
        if not row:
            return None
        return {**_schedule_row(row), "client_name": row["client_name"], "hostname": row["hostname"]}


def create_schedule(client_id: str, name: str, enabled: bool, days: list[int], hour: int, minute: int) -> dict[str, Any]:
    now = time.time()
    schedule_id = str(uuid.uuid4())
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO schedules (id, client_id, name, enabled, days_json, hour, minute, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (schedule_id, client_id, name, int(enabled), json.dumps(days), hour, minute, now, now),
        )
    return get_schedule(schedule_id)  # type: ignore[return-value]


def update_schedule(schedule_id: str, name: str | None, enabled: bool | None, days: list[int] | None, hour: int | None, minute: int | None) -> dict[str, Any] | None:
    current = get_schedule(schedule_id)
    if not current:
        return None
    with connect() as conn:
        conn.execute(
            """
            UPDATE schedules SET
                name = ?, enabled = ?, days_json = ?, hour = ?, minute = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                name if name is not None else current["name"],
                int(enabled if enabled is not None else current["enabled"]),
                json.dumps(days if days is not None else current["days"]),
                hour if hour is not None else current["hour"],
                minute if minute is not None else current["minute"],
                time.time(),
                schedule_id,
            ),
        )
    return get_schedule(schedule_id)


def delete_schedule(schedule_id: str) -> bool:
    with connect() as conn:
        cur = conn.execute("DELETE FROM schedules WHERE id = ?", (schedule_id,))
        return cur.rowcount > 0


def mark_schedule_run(schedule_id: str) -> None:
    with connect() as conn:
        conn.execute("UPDATE schedules SET last_run_at = ? WHERE id = ?", (time.time(), schedule_id))


def queue_trigger(client_id: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM pending_cancels WHERE client_id = ?", (client_id,))
        conn.execute(
            "INSERT OR REPLACE INTO pending_triggers (client_id, created_at) VALUES (?, ?)",
            (client_id, time.time()),
        )


def clear_trigger(client_id: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM pending_triggers WHERE client_id = ?", (client_id,))


def pop_trigger(client_id: str) -> bool:
    with connect() as conn:
        row = conn.execute("SELECT 1 FROM pending_triggers WHERE client_id = ?", (client_id,)).fetchone()
        if row:
            conn.execute("DELETE FROM pending_triggers WHERE client_id = ?", (client_id,))
            return True
        return False


def request_cancel(client_id: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM pending_triggers WHERE client_id = ?", (client_id,))
        conn.execute(
            "INSERT OR REPLACE INTO pending_cancels (client_id, created_at) VALUES (?, ?)",
            (client_id, time.time()),
        )


def pop_cancel(client_id: str) -> bool:
    with connect() as conn:
        row = conn.execute("SELECT 1 FROM pending_cancels WHERE client_id = ?", (client_id,)).fetchone()
        if row:
            conn.execute("DELETE FROM pending_cancels WHERE client_id = ?", (client_id,))
            return True
        return False


def cancel_running_runs(client_id: str, message: str = "Stopped from dashboard") -> int:
    now = time.time()
    with connect() as conn:
        cur = conn.execute(
            """
            UPDATE backup_runs
            SET status = 'error', phase = 'cancelled', message = ?, finished_at = ?
            WHERE client_id = ? AND status = 'running'
            """,
            (message, now, client_id),
        )
        return int(cur.rowcount)


def get_backup_run(run_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM backup_runs WHERE id = ?", (run_id,)).fetchone()
        return dict(row) if row else None


# Ignore zombie "running" rows older than this so stuck dashboard state
# cannot permanently suppress schedules (single-flight only covers live work).
ACTIVE_BACKUP_MAX_AGE_SECONDS = 6 * 3600


def client_has_active_run(client_id: str, max_age_seconds: float = ACTIVE_BACKUP_MAX_AGE_SECONDS) -> bool:
    """True when this client has a recent backup_run still marked running."""
    cutoff = time.time() - max_age_seconds
    with connect() as conn:
        row = conn.execute(
            """
            SELECT 1 FROM backup_runs
            WHERE client_id = ? AND status = 'running' AND started_at >= ?
            LIMIT 1
            """,
            (client_id, cutoff),
        ).fetchone()
        return row is not None


def create_backup_run(client_id: str, triggered_by: str, schedule_id: str | None = None) -> str:
    run_id = str(uuid.uuid4())
    now = time.time()
    with connect() as conn:
        conn.execute("DELETE FROM pending_cancels WHERE client_id = ?", (client_id,))
        conn.execute(
            "INSERT INTO backup_runs (id, client_id, schedule_id, status, phase, message, started_at, triggered_by) VALUES (?, ?, ?, 'running', 'starting', '', ?, ?)",
            (run_id, client_id, schedule_id, now, triggered_by),
        )
    return run_id


def update_backup_run(run_id: str, status: str | None = None, phase: str | None = None, message: str | None = None) -> bool:
    """Update a run. Returns False if ignored (finished/cancelled runs are immutable)."""
    with connect() as conn:
        row = conn.execute(
            "SELECT status, finished_at, phase FROM backup_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        if not row:
            return False
        # Dashboard Stop (or prior finish) wins — ignore late Mac status updates.
        if row["finished_at"] is not None:
            return False
        fields, values = [], []
        if status is not None:
            fields.append("status = ?"); values.append(status)
        if phase is not None:
            fields.append("phase = ?"); values.append(phase)
        if message is not None:
            fields.append("message = ?"); values.append(message)
        if status in ("success", "error"):
            fields.append("finished_at = ?"); values.append(time.time())
        if not fields:
            return True
        values.append(run_id)
        conn.execute(f"UPDATE backup_runs SET {', '.join(fields)} WHERE id = ?", values)
        return True


def list_backup_runs(limit: int = 50) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT r.*, c.name AS client_name, s.name AS schedule_name
            FROM backup_runs r
            JOIN clients c ON c.id = r.client_id
            LEFT JOIN schedules s ON s.id = r.schedule_id
            ORDER BY r.started_at DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def touch_client(client_id: str) -> None:
    with connect() as conn:
        conn.execute("UPDATE clients SET last_seen_at = ? WHERE id = ?", (time.time(), client_id))
