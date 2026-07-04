from __future__ import annotations

import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
STATE_DIR = Path(os.environ.get("STATE_DIR", "/state"))
JSONL_PATH = DATA_DIR / "messages.jsonl"
CONTACTS_PATH = DATA_DIR / "contacts.json"
HTML_DIR = DATA_DIR / "html-export"
HTML_ATTACHMENTS_DIR = HTML_DIR / "attachments"
RAW_DIR = DATA_DIR / "raw"
ATTACHMENTS_DIR = RAW_DIR / "Attachments"
DB_PATH = STATE_DIR / "archive.db"

QDRANT_URL = os.environ.get("QDRANT_URL", "http://qdrant:6333")
COLLECTION = os.environ.get("COLLECTION", "imessages")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "BAAI/bge-small-en-v1.5")
BATCH_SIZE = int(os.environ.get("BATCH_SIZE", "64"))

IMMICH_URL = os.environ.get("IMMICH_URL", "").rstrip("/")
IMMICH_API_KEY = os.environ.get("IMMICH_API_KEY", "")
IMMICH_ALBUM = os.environ.get("IMMICH_ALBUM", "iMessage")
