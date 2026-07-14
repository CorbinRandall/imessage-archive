#!/usr/bin/env python3
"""Upload iMessage attachments to Immich and patch messages.jsonl with asset IDs."""
from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

DEVICE_ID = "imessage-archive"
BATCH = 100
UPLOAD_MIMES = ("image/", "video/", "image/gif")


def log(msg: str) -> None:
    print(msg, flush=True)


def api_json(url: str, api_key: str, method: str = "GET", body: dict | None = None) -> dict | list:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    req.add_header("x-api-key", api_key)
    with urllib.request.urlopen(req, timeout=120) as resp:
        raw = resp.read().decode()
        return json.loads(raw) if raw else {}


def sha1_file(path: Path) -> str:
    h = hashlib.sha1()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def iso_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def resolve_file(att: dict, html_attachments: Path, raw_root: Path) -> Path | None:
    att_id = att.get("attachment_id")
    for rel in att.get("paths") or ([att["path"]] if att.get("path") else []):
        if not rel:
            continue
        if rel.startswith("html-export/"):
            p = html_attachments.parent.parent / rel.removeprefix("html-export/")
        elif rel.startswith("raw/"):
            p = raw_root.parent / rel.removeprefix("raw/")
        else:
            p = raw_root / rel
        if p.is_file():
            return p
    if att_id is not None and html_attachments.exists():
        matches = list(html_attachments.rglob(f"{att_id}.*"))
        if matches:
            return matches[0]
    return None


def should_upload(att: dict) -> bool:
    mime = (att.get("mime_type") or "").lower()
    name = (att.get("name") or "").lower()
    if any(mime.startswith(p) for p in UPLOAD_MIMES):
        return True
    if name.endswith((".gif", ".heic", ".heic", ".mov", ".mp4", ".m4v", ".jpg", ".jpeg", ".png", ".webp")):
        return True
    return False


def bulk_check(base: str, api_key: str, items: list[tuple[str, str]]) -> dict[str, dict]:
    """items: [(client_id, checksum)] -> {client_id: {action, assetId}}"""
    out: dict[str, dict] = {}
    for i in range(0, len(items), BATCH):
        batch = items[i : i + BATCH]
        payload = {"assets": [{"id": cid, "checksum": chk} for cid, chk in batch]}
        resp = api_json(f"{base}/assets/bulk-upload-check", api_key, "POST", payload)
        for row in resp.get("results") or resp if isinstance(resp, list) else []:
            if isinstance(row, dict):
                out[row.get("id", "")] = row
    return out


def upload_file(base: str, api_key: str, path: Path, client_id: str, checksum: str) -> str | None:
    import uuid

    boundary = f"----imessage{uuid.uuid4().hex}"
    filename = path.name
    mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    now = iso_now()

    parts: list[bytes] = []
    for key, val in [
        ("fileCreatedAt", now),
        ("fileModifiedAt", now),
        ("filename", filename),
    ]:
        parts.append(f"--{boundary}\r\n".encode())
        parts.append(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode())
        parts.append(f"{val}\r\n".encode())

    parts.append(f"--{boundary}\r\n".encode())
    parts.append(
        f'Content-Disposition: form-data; name="assetData"; filename="{filename}"\r\n'.encode()
    )
    parts.append(f"Content-Type: {mime}\r\n\r\n".encode())
    parts.append(path.read_bytes())
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    body = b"".join(parts)

    req = urllib.request.Request(f"{base}/assets", data=body, method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    req.add_header("x-api-key", api_key)
    req.add_header("x-immich-checksum", checksum)
    req.add_header("Accept", "application/json")

    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            data = json.loads(resp.read().decode())
            return data.get("id") or data.get("asset", {}).get("id")
    except urllib.error.HTTPError as exc:
        err = exc.read().decode()[:500]
        log(f"WARN upload {filename}: HTTP {exc.code} {err}")
        return None


def ensure_album(base: str, api_key: str, name: str) -> str:
    albums = api_json(f"{base}/albums", api_key)
    if isinstance(albums, list):
        for alb in albums:
            if (alb.get("albumName") or alb.get("name")) == name:
                return alb["id"]
    created = api_json(f"{base}/albums", api_key, "POST", {"albumName": name, "description": "iMessage attachments"})
    return created["id"]


def add_to_album(base: str, api_key: str, album_id: str, asset_ids: list[str]) -> None:
    for i in range(0, len(asset_ids), BATCH):
        batch = asset_ids[i : i + BATCH]
        api_json(f"{base}/albums/{album_id}/assets", api_key, "PUT", {"ids": batch})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jsonl", required=True)
    parser.add_argument("--html-dir", required=True, help="Local html export dir (contains attachments/)")
    parser.add_argument("--raw-dir", default="", help="Local raw export dir for fallback paths")
    parser.add_argument("--immich-url", default="http://192.168.1.200:8090")
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--album", default="iMessage")
    parser.add_argument("--map-file", default="", help="Optional sidecar JSON map attachment_id->asset_id")
    args = parser.parse_args()

    base = args.immich_url.rstrip("/") + "/api"
    jsonl_path = Path(args.jsonl).expanduser()
    html_dir = Path(args.html_dir).expanduser()
    html_att = html_dir / "attachments"
    raw_root = Path(args.raw_dir).expanduser() if args.raw_dir else html_dir.parent / "raw"

    # Ping
    try:
        api_json(f"{base}/server/ping", args.api_key)
    except Exception as exc:
        log(f"ERROR: Cannot reach Immich at {args.immich_url}: {exc}")
        return 1

    messages: list[dict] = []
    with jsonl_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                messages.append(json.loads(line))

    sidecar: dict[str, str] = {}
    if args.map_file:
        map_path = Path(args.map_file).expanduser()
        if map_path.exists():
            sidecar = {str(k): v for k, v in json.loads(map_path.read_text()).items()}

    # Always stamp Immich IDs from the sidecar onto the working JSONL first.
    stamped = 0
    for msg in messages:
        for att in msg.get("attachments") or []:
            att_id = att.get("attachment_id")
            if att_id is None:
                continue
            mapped = sidecar.get(str(att_id))
            if mapped and not att.get("immich_asset_id"):
                att["immich_asset_id"] = mapped
                stamped += 1
    if stamped:
        log(f"Stamped {stamped} immich_asset_id values from map sidecar")

    # Collect work items
    work: list[tuple[dict, dict, Path, str, str]] = []  # msg, att, path, client_id, checksum
    seen_ids: set[str] = set()
    for msg in messages:
        for att in msg.get("attachments") or []:
            if not should_upload(att):
                continue
            att_id = att.get("attachment_id")
            if att_id is None:
                continue
            if att.get("immich_asset_id") or sidecar.get(str(att_id)):
                if sidecar.get(str(att_id)) and not att.get("immich_asset_id"):
                    att["immich_asset_id"] = sidecar[str(att_id)]
                continue
            client_id = f"imessage:{att_id}"
            if client_id in seen_ids:
                continue
            path = resolve_file(att, html_att, raw_root)
            if not path:
                continue
            seen_ids.add(client_id)
            work.append((msg, att, path, client_id, sha1_file(path)))

    log(f"Immich upload: {len(work)} new media files to process")

    if not work:
        log("Nothing to upload — rewriting JSONL with stamped map IDs")
        with jsonl_path.open("w", encoding="utf-8") as out:
            for msg in messages:
                out.write(json.dumps(msg, ensure_ascii=False) + "\n")
        if args.map_file:
            map_path = Path(args.map_file).expanduser()
            map_path.parent.mkdir(parents=True, exist_ok=True)
            for msg in messages:
                for att in msg.get("attachments") or []:
                    aid = att.get("attachment_id")
                    iid = att.get("immich_asset_id")
                    if aid is not None and iid:
                        sidecar[str(aid)] = iid
            map_path.write_text(json.dumps(sidecar, indent=2), encoding="utf-8")
        return 0

    checks = bulk_check(base, args.api_key, [(w[3], w[4]) for w in work])
    uploaded_ids: list[str] = []
    patched = 0
    total = len(work)

    def progress(i: int, label: str) -> None:
        pct = (i / total) * 100.0 if total else 100.0
        width = 28
        filled = int(round(width * pct / 100.0))
        bar = "[" + "#" * filled + "-" * (width - filled) + f"] {pct:5.1f}%"
        line = f"Immich {bar}  {i}/{total}  {label[:40]}"
        if sys.stdout.isatty():
            print(f"\r{line:<90}", end="", flush=True)
        elif i == total or i % 10 == 0 or i == 1:
            log(line)

    for idx, (_msg, att, path, client_id, checksum) in enumerate(work, start=1):
        row = checks.get(client_id, {})
        action = row.get("action", "accept")
        asset_id = row.get("assetId")

        if action == "duplicate" and asset_id:
            att["immich_asset_id"] = asset_id
            patched += 1
            progress(idx, f"skip duplicate {path.name}")
            continue

        if action == "reject":
            log(f"WARN rejected {path.name}")
            progress(idx, f"rejected {path.name}")
            continue

        progress(idx, f"uploading {path.name}")
        asset_id = upload_file(base, args.api_key, path, client_id, checksum)
        if asset_id:
            att["immich_asset_id"] = asset_id
            uploaded_ids.append(asset_id)
            patched += 1

    if sys.stdout.isatty():
        print()

    if uploaded_ids:
        album_id = ensure_album(base, args.api_key, args.album)
        add_to_album(base, args.api_key, album_id, uploaded_ids)
        log(f"Added {len(uploaded_ids)} assets to album '{args.album}'")

    # Rewrite JSONL
    with jsonl_path.open("w", encoding="utf-8") as out:
        for msg in messages:
            out.write(json.dumps(msg, ensure_ascii=False) + "\n")

    if args.map_file:
        map_path = Path(args.map_file).expanduser()
        map_path.parent.mkdir(parents=True, exist_ok=True)
        for msg in messages:
            for att in msg.get("attachments") or []:
                aid = att.get("attachment_id")
                iid = att.get("immich_asset_id")
                if aid is not None and iid:
                    sidecar[str(aid)] = iid
        map_path.write_text(json.dumps(sidecar, indent=2), encoding="utf-8")

    log(f"Done — patched {patched} attachments with immich_asset_id")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
