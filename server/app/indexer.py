from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

from fastembed import TextEmbedding
from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

logger = logging.getLogger(__name__)

from app.config import BATCH_SIZE, COLLECTION, DATA_DIR, EMBED_MODEL, JSONL_PATH, QDRANT_URL

_index_lock = threading.Lock()
_index_state: dict[str, Any] = {
    "running": False,
    "last_started": None,
    "last_finished": None,
    "last_error": None,
    "indexed": 0,
    "total": 0,
}


def _point_id(message_key: str) -> int:
    digest = hashlib.sha256(message_key.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


class MessageIndexer:
    def __init__(self) -> None:
        self._embedder: TextEmbedding | None = None
        self._qdrant: QdrantClient | None = None
        self._vector_size: int | None = None

    @property
    def embedder(self) -> TextEmbedding:
        if self._embedder is None:
            logger.info("Loading embedding model: %s", EMBED_MODEL)
            self._embedder = self._load_embedder()
        return self._embedder

    @staticmethod
    def _load_embedder() -> TextEmbedding:
        """Load the embedding model on GPU (CUDA) when available, falling back to CPU."""
        try:
            embedder = TextEmbedding(
                model_name=EMBED_MODEL,
                providers=["CUDAExecutionProvider", "CPUExecutionProvider"],
            )
            active = embedder.model.model.get_providers()
            if "CUDAExecutionProvider" in active:
                logger.info("Embedding model running on GPU (CUDAExecutionProvider)")
            else:
                logger.warning("CUDA requested but not active; providers=%s", active)
            return embedder
        except Exception:  # noqa: BLE001
            logger.exception("GPU embedding init failed; falling back to CPU")
            return TextEmbedding(model_name=EMBED_MODEL, providers=["CPUExecutionProvider"])

    @property
    def qdrant(self) -> QdrantClient:
        if self._qdrant is None:
            self._qdrant = QdrantClient(url=QDRANT_URL)
        return self._qdrant

    def ensure_collection(self, vector_size: int) -> None:
        if not self.qdrant.collection_exists(COLLECTION):
            self.qdrant.create_collection(
                collection_name=COLLECTION,
                vectors_config=qmodels.VectorParams(size=vector_size, distance=qmodels.Distance.COSINE),
            )
            self.qdrant.create_payload_index(COLLECTION, "chat", qmodels.PayloadSchemaType.KEYWORD)
            self.qdrant.create_payload_index(COLLECTION, "sender", qmodels.PayloadSchemaType.KEYWORD)
            self.qdrant.create_payload_index(COLLECTION, "date", qmodels.PayloadSchemaType.KEYWORD)

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [vec.tolist() for vec in self.embedder.embed(texts)]

    def load_messages(self) -> list[dict[str, Any]]:
        if not JSONL_PATH.exists():
            return []
        messages: list[dict[str, Any]] = []
        with JSONL_PATH.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                messages.append(json.loads(line))
        return messages

    def build_search_text(self, msg: dict[str, Any]) -> str:
        chat = msg.get("chat") or "Unknown chat"
        sender = msg.get("sender") or ("Me" if msg.get("is_from_me") else "Them")
        text = msg.get("text") or ""
        participants = ", ".join(msg.get("participants") or [])
        return f"Chat: {chat}\nParticipants: {participants}\nFrom: {sender}\n{text}"

    def index_all(self) -> dict[str, Any]:
        with _index_lock:
            if _index_state["running"]:
                return {"status": "already_running", **_index_state}
            _index_state["running"] = True
            _index_state["last_started"] = time.time()
            _index_state["last_error"] = None
            _index_state["indexed"] = 0

        try:
            messages = self.load_messages()
            _index_state["total"] = len(messages)
            if not messages:
                return {"status": "no_data", "message": f"No messages at {JSONL_PATH}"}

            sample_vec = self.embed(["warmup"])[0]
            self.ensure_collection(len(sample_vec))

            batch_texts: list[str] = []
            batch_msgs: list[dict[str, Any]] = []
            for idx, msg in enumerate(messages):
                batch_texts.append(self.build_search_text(msg))
                batch_msgs.append(msg)

                if len(batch_texts) < BATCH_SIZE and idx + 1 < len(messages):
                    continue

                vectors = self.embed(batch_texts)
                points = []
                for msg, vector in zip(batch_msgs, vectors):
                    point_id = _point_id(str(msg.get("id") or f"{msg.get('chat_id')}:{msg.get('message_id')}"))
                    points.append(
                        qmodels.PointStruct(
                            id=point_id,
                            vector=vector,
                            payload={
                                "id": msg.get("id"),
                                "chat": msg.get("chat"),
                                "sender": msg.get("sender"),
                                "is_from_me": msg.get("is_from_me"),
                                "date": msg.get("date"),
                                "service": msg.get("service"),
                                "text": msg.get("text"),
                                "participants": msg.get("participants") or [],
                                "has_attachments": msg.get("has_attachments"),
                            },
                        )
                    )

                self.qdrant.upsert(collection_name=COLLECTION, points=points)
                _index_state["indexed"] = idx + 1
                batch_texts = []
                batch_msgs = []

            return {"status": "ok", "indexed": len(messages)}
        except Exception as exc:  # noqa: BLE001
            logger.exception("Index failed")
            _index_state["last_error"] = str(exc)
            return {"status": "error", "error": str(exc)}
        finally:
            _index_state["running"] = False
            _index_state["last_finished"] = time.time()

    def search(self, query: str, limit: int = 20, chat: str | None = None) -> list[dict[str, Any]]:
        if not self.qdrant.collection_exists(COLLECTION):
            return []

        vector = self.embed([query])[0]
        query_filter = None
        if chat:
            query_filter = qmodels.Filter(
                must=[qmodels.FieldCondition(key="chat", match=qmodels.MatchValue(value=chat))]
            )

        hits = self.qdrant.search(
            collection_name=COLLECTION,
            query_vector=vector,
            limit=limit,
            query_filter=query_filter,
            with_payload=True,
        )

        results: list[dict[str, Any]] = []
        for hit in hits:
            payload = hit.payload or {}
            results.append(
                {
                    "score": hit.score,
                    "chat": payload.get("chat"),
                    "sender": payload.get("sender"),
                    "date": payload.get("date"),
                    "text": payload.get("text"),
                    "participants": payload.get("participants"),
                    "has_attachments": payload.get("has_attachments"),
                }
            )
        return results


indexer = MessageIndexer()
