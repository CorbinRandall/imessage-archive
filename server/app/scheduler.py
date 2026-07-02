from __future__ import annotations

from datetime import datetime
from typing import Any

from app import db


def should_run_scheduled_backup(
    schedule: dict[str, Any],
    last_run_at: float | None,
    now: datetime | None = None,
) -> bool:
    if not schedule.get("enabled"):
        return False
    days = schedule.get("days") or []
    if not days:
        return False

    now = now or datetime.now()
    if now.weekday() not in days:
        return False
    if now.hour != schedule.get("hour", 0) or now.minute != schedule.get("minute", 0):
        return False

    if last_run_at:
        last = datetime.fromtimestamp(last_run_at)
        if last.date() == now.date() and last.hour == now.hour and last.minute == now.minute:
            return False
    return True


def client_heartbeat(client_id: str) -> dict[str, Any]:
    db.touch_client(client_id)
    manual = db.pop_trigger(client_id)
    schedules = db.list_schedules(client_id)
    due_schedule = None

    if not manual:
        now = datetime.now()
        for sched in schedules:
            if sched.get("enabled") and should_run_scheduled_backup(sched, sched.get("last_run_at"), now):
                due_schedule = sched
                break

    return {
        "trigger_backup": manual or due_schedule is not None,
        "trigger_reason": "manual" if manual else ("schedule" if due_schedule else None),
        "schedule_id": due_schedule["id"] if due_schedule else None,
        "schedules": schedules,
    }
