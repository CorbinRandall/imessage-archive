from __future__ import annotations

from datetime import datetime
from typing import Any

from app import db


def should_run_scheduled_backup(
    schedule: dict[str, Any],
    last_run_at: float | None,
    now: datetime | None = None,
) -> bool:
    """True when today's scheduled time has arrived and we have not already started a run today.

    Uses the server process local timezone (set TZ=America/Los_Angeles in compose).
    Catch-up: if the Mac agent was asleep at HH:MM, the next heartbeat later that day
    still fires once — previously we required an exact-minute match and silently skipped.
    """
    if not schedule.get("enabled"):
        return False
    days = schedule.get("days") or []
    if not days:
        return False

    now = now or datetime.now()
    if now.weekday() not in days:
        return False

    scheduled_today = now.replace(
        hour=int(schedule.get("hour", 0)),
        minute=int(schedule.get("minute", 0)),
        second=0,
        microsecond=0,
    )
    if now < scheduled_today:
        return False

    if last_run_at:
        last = datetime.fromtimestamp(last_run_at)
        if last.date() == now.date():
            return False
    return True


def client_heartbeat(client_id: str) -> dict[str, Any]:
    db.touch_client(client_id)
    # Honor dashboard Stop before starting anything new.
    if db.pop_cancel(client_id):
        db.clear_trigger(client_id)
        db.cancel_running_runs(client_id, "Stopped from dashboard")
        schedules = db.list_schedules(client_id)
        return {
            "trigger_backup": False,
            "trigger_reason": None,
            "schedule_id": None,
            "cancel_backup": True,
            "schedule_deferred": False,
            "active_run": False,
            "schedules": schedules,
        }

    manual = db.pop_trigger(client_id)
    schedules = db.list_schedules(client_id)
    due_schedule = None
    active = db.client_has_active_run(client_id)
    schedule_deferred = False

    # Single-flight: never start a scheduled backup while one is already active.
    # Manual triggers still go through (dashboard "Backup now"). Do not mark
    # schedule last_run_at here — catch-up can fire on a later heartbeat.
    if not manual:
        if active:
            now = datetime.now()
            schedule_deferred = any(
                sched.get("enabled") and should_run_scheduled_backup(sched, sched.get("last_run_at"), now)
                for sched in schedules
            )
        else:
            now = datetime.now()
            for sched in schedules:
                if sched.get("enabled") and should_run_scheduled_backup(sched, sched.get("last_run_at"), now):
                    due_schedule = sched
                    break

    return {
        "trigger_backup": manual or due_schedule is not None,
        "trigger_reason": "manual" if manual else ("schedule" if due_schedule else None),
        "schedule_id": due_schedule["id"] if due_schedule else None,
        "cancel_backup": False,
        "schedule_deferred": schedule_deferred,
        "active_run": active,
        "schedules": schedules,
    }
