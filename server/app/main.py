from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.indexer import JSONL_PATH, _index_state, indexer

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="iMessage Vector Search", version="1.0.0")
static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")


class IndexRequest(BaseModel):
    full: bool = True


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "jsonl_exists": JSONL_PATH.exists(),
        "jsonl_path": str(JSONL_PATH),
        "index": _index_state,
    }


@app.get("/", response_class=HTMLResponse)
def home() -> FileResponse:
    return FileResponse(static_dir / "index.html")


@app.get("/api/stats")
def stats() -> dict[str, Any]:
    count = 0
    if JSONL_PATH.exists():
        with JSONL_PATH.open("r", encoding="utf-8") as handle:
            count = sum(1 for _ in handle)
    return {"messages_in_jsonl": count, "index": _index_state}


@app.post("/index")
def start_index(req: IndexRequest) -> dict[str, Any]:
    if _index_state["running"]:
        return {"status": "already_running", "index": _index_state}

    def run() -> None:
        indexer.index_all()

    threading.Thread(target=run, daemon=True).start()
    return {"status": "started", "full": req.full}


@app.get("/search")
def search(
    q: str = Query(..., min_length=1),
    limit: int = Query(20, ge=1, le=100),
    chat: str | None = None,
) -> dict[str, Any]:
    results = indexer.search(q, limit=limit, chat=chat)
    return {"query": q, "count": len(results), "results": results}
