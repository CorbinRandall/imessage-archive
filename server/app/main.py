from __future__ import annotations

import logging
import mimetypes
import threading
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import archive, db, scheduler
from app.config import DATA_DIR, HTML_DIR, JSONL_PATH, STATE_DIR
from app.indexer import _index_state, indexer

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="iMessage Archive", version="2.0.0")
static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.on_event("startup")
def startup() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    db.init_db()


# --- Auth helper ---

def client_from_token(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    client = db.get_client_by_token(token)
    if not client:
        raise HTTPException(401, "Invalid token")
    return client


# --- Schemas ---

class RegisterRequest(BaseModel):
    name: str
    hostname: str


class ScheduleCreate(BaseModel):
    client_id: str
    name: str = "Default"
    enabled: bool = True
    days: list[int] = Field(default_factory=list)
    hour: int = Field(3, ge=0, le=23)
    minute: int = Field(0, ge=0, le=59)


class ScheduleUpdate(BaseModel):
    name: str | None = None
    enabled: bool | None = None
    days: list[int] | None = None
    hour: int | None = Field(None, ge=0, le=23)
    minute: int | None = Field(None, ge=0, le=59)


class ScheduleUpdateLegacy(BaseModel):
    enabled: bool = False
    days: list[int] = Field(default_factory=list)
    hour: int = Field(3, ge=0, le=23)
    minute: int = Field(0, ge=0, le=59)


class BackupStatusUpdate(BaseModel):
    run_id: str
    status: str | None = None
    phase: str | None = None
    message: str | None = None


class BackupStartRequest(BaseModel):
    triggered_by: str = "agent"
    schedule_id: str | None = None


class IndexRequest(BaseModel):
    full: bool = True


# --- UI ---

@app.get("/")
def home() -> FileResponse:
    return FileResponse(static_dir / "index.html")


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "jsonl_exists": JSONL_PATH.exists(),
        "index": _index_state,
        "stats": archive.archive_stats(),
    }


# --- Archive browse ---

@app.get("/api/stats")
def stats() -> dict[str, Any]:
    return {"archive": archive.archive_stats(), "index": _index_state}


@app.get("/api/chats")
def get_chats() -> dict[str, Any]:
    return {"chats": archive.list_chats()}


@app.get("/api/chats/{chat_id}/messages")
def get_chat_messages(chat_id: int, limit: int = Query(500, le=2000), offset: int = 0) -> dict[str, Any]:
    return {"chat_id": chat_id, "messages": archive.chat_messages(chat_id, limit, offset)}


@app.get("/api/html")
def list_html() -> dict[str, Any]:
    return {"exports": archive.list_html_exports()}


@app.get("/api/html/{filename}")
def get_html_export(filename: str) -> FileResponse:
    path = (HTML_DIR / Path(filename).name).resolve()
    if not path.exists() or not str(path).startswith(str(HTML_DIR.resolve())):
        raise HTTPException(404, "Export not found")
    return FileResponse(path)


@app.get("/api/media/{path:path}")
def get_media(path: str) -> FileResponse:
    resolved = archive.resolve_media_path(path)
    if not resolved:
        raise HTTPException(404, "Media not found")
    media_type, _ = mimetypes.guess_type(str(resolved))
    return FileResponse(resolved, media_type=media_type or "application/octet-stream")


# --- Search ---

@app.post("/index")
@app.post("/api/index")
def start_index(req: IndexRequest) -> dict[str, Any]:
    if _index_state["running"]:
        return {"status": "already_running", "index": _index_state}

    def run() -> None:
        indexer.index_all()

    threading.Thread(target=run, daemon=True).start()
    return {"status": "started", "full": req.full}


@app.get("/search")
@app.get("/api/search")
def search(q: str = Query(..., min_length=1), limit: int = Query(25, ge=1, le=100), chat: str | None = None) -> dict[str, Any]:
    results = indexer.search(q, limit=limit, chat=chat)
    return {"query": q, "count": len(results), "results": results}


# --- Clients (Mac agents) ---

@app.post("/api/clients/register")
def register_client(req: RegisterRequest) -> dict[str, Any]:
    client = db.register_client(req.name.strip(), req.hostname.strip())
    return {
        "id": client["id"],
        "token": client["token"],
        "name": client["name"],
    }


@app.get("/api/clients")
def get_clients() -> dict[str, Any]:
    return {"clients": db.list_clients(), "runs": db.list_backup_runs(20)}


@app.post("/api/clients/heartbeat")
def heartbeat(client: dict[str, Any] = Depends(client_from_token)) -> dict[str, Any]:
    return scheduler.client_heartbeat(client["id"])


@app.get("/api/contacts/stats")
def contacts_stats() -> dict[str, Any]:
    from app.archive import load_contacts
    contacts = load_contacts()
    return {"count": len(contacts)}


# --- Schedules CRUD ---

@app.get("/api/schedules")
def get_schedules(client_id: str | None = None) -> dict[str, Any]:
    return {"schedules": db.list_schedules(client_id)}


@app.get("/api/schedules/{schedule_id}")
def get_schedule_by_id(schedule_id: str) -> dict[str, Any]:
    sched = db.get_schedule(schedule_id)
    if not sched:
        raise HTTPException(404, "Schedule not found")
    return sched


@app.post("/api/schedules")
def create_schedule(req: ScheduleCreate) -> dict[str, Any]:
    sched = db.create_schedule(req.client_id, req.name, req.enabled, req.days, req.hour, req.minute)
    return {"schedule": sched}


@app.put("/api/schedules/{schedule_id}")
def update_schedule_by_id(schedule_id: str, req: ScheduleUpdate) -> dict[str, Any]:
    sched = db.update_schedule(schedule_id, req.name, req.enabled, req.days, req.hour, req.minute)
    if not sched:
        raise HTTPException(404, "Schedule not found")
    return {"schedule": sched}


@app.delete("/api/schedules/{schedule_id}")
def delete_schedule_by_id(schedule_id: str) -> dict[str, Any]:
    if not db.delete_schedule(schedule_id):
        raise HTTPException(404, "Schedule not found")
    return {"ok": True}


@app.put("/api/clients/{client_id}/schedule")
def set_schedule_legacy(client_id: str, req: ScheduleUpdateLegacy) -> dict[str, Any]:
    """Legacy: upsert a single 'Default' schedule for a client."""
    existing = [s for s in db.list_schedules(client_id) if s["name"] == "Default"]
    if existing:
        sched = db.update_schedule(existing[0]["id"], "Default", req.enabled, req.days, req.hour, req.minute)
    else:
        sched = db.create_schedule(client_id, "Default", req.enabled, req.days, req.hour, req.minute)
    return {"ok": True, "schedule": sched}


@app.post("/api/clients/{client_id}/schedule/run")
def mark_schedule_ran(schedule_id: str = Query(...)) -> dict[str, Any]:
    db.mark_schedule_run(schedule_id)
    return {"ok": True}


@app.post("/api/clients/{client_id}/backup/trigger")
def trigger_backup(client_id: str) -> dict[str, Any]:
    db.queue_trigger(client_id)
    return {"ok": True, "queued": True}


@app.post("/api/clients/backup/start")
def backup_start(req: BackupStartRequest, client: dict[str, Any] = Depends(client_from_token)) -> dict[str, Any]:
    run_id = db.create_backup_run(client["id"], req.triggered_by, req.schedule_id)
    if req.schedule_id:
        db.mark_schedule_run(req.schedule_id)
    return {"run_id": run_id}


@app.post("/api/clients/backup/status")
def backup_status(req: BackupStatusUpdate, client: dict[str, Any] = Depends(client_from_token)) -> dict[str, Any]:
    db.update_backup_run(req.run_id, req.status, req.phase, req.message)
    return {"ok": True}
