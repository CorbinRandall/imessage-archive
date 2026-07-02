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


class ScheduleUpdate(BaseModel):
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


@app.put("/api/clients/{client_id}/schedule")
def set_schedule(client_id: str, req: ScheduleUpdate) -> dict[str, Any]:
    db.update_schedule(client_id, req.enabled, req.days, req.hour, req.minute)
    return {"ok": True, "schedule": db.get_schedule(client_id)}


@app.post("/api/clients/{client_id}/backup/trigger")
def trigger_backup(client_id: str) -> dict[str, Any]:
    db.queue_trigger(client_id)
    return {"ok": True, "queued": True}


@app.post("/api/clients/backup/start")
def backup_start(req: BackupStartRequest, client: dict[str, Any] = Depends(client_from_token)) -> dict[str, Any]:
    run_id = db.create_backup_run(client["id"], req.triggered_by)
    return {"run_id": run_id}


@app.post("/api/clients/backup/status")
def backup_status(req: BackupStatusUpdate, client: dict[str, Any] = Depends(client_from_token)) -> dict[str, Any]:
    db.update_backup_run(req.run_id, req.status, req.phase, req.message)
    return {"ok": True}
