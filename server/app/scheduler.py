from __future__ import annotations

from datetime import datetime
from typing import Any

from app import db


def should_run_scheduled_backup(schedule: dict[str, Any], last_backup_at: float | None, now: datetime | None = None) -> bool:
    if not schedule.get("enabled"):
        return False
    days = schedule.get("days") or []
    if not days:
        return False

    now = now or datetime.now()
    # Python weekday: Mon=0 .. Sun=6; UI uses same
    if now.weekday() not in days:
        return False
    if now.hour != schedule.get("hour", 0) or now.minute != schedule.get("minute", 0):
        return False

    if last_backup_at:
        last = datetime.fromtimestamp(last_backup_at)
        if last.date() == now.date() and last.hour == now.hour and last.minute == now.minute:
            return False
    return True


def client_heartbeat(client_id: str) -> dict[str, Any]:
    db.touch_client(client_id)
    trigger = db.pop_trigger(client_id)
    schedule = db.get_schedule(client_id) or {"enabled": False, "days": [], "hour": 3, "minute": 0}

    clients = db.list_clients()
    me = next((c for c in clients if c["id"] == client_id), None)
    last_backup_at = me.get("last_backup_at") if me else None

    scheduled = False
    if not trigger and schedule.get("enabled"):
        scheduled = should_run_scheduled_backup(schedule, last_backup_at)

    return {
        "trigger_backup": trigger or scheduled,
        "trigger_reason": "manual" if trigger else ("schedule" if scheduled else None),
        "schedule": schedule,
    }
