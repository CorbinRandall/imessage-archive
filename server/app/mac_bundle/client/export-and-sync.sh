#!/usr/bin/env bash
# Export iMessages from Mac, sync to server, trigger vector reindex.
set -euo pipefail

# Kill child processes (rsync, python uploads) when agent Stop cancels the group.
trap 'trap - EXIT INT TERM; pkill -P $$ 2>/dev/null || true' EXIT INT TERM

# launchd agents get a minimal PATH — include Homebrew and user bins.
export PATH="/opt/homebrew/bin:/usr/local/bin:${HOME}/bin:/usr/bin:/bin:/usr/sbin:/sbin"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="${CONFIG_FILE:-$HOME/.config/imessage-archive.env}"
CHECKPOINT_FILE="${CHECKPOINT_FILE:-$HOME/.config/imessage-archive-checkpoint.json}"

if [[ -f "$CONFIG_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$CONFIG_FILE"
fi

UNRAID_HOST="${UNRAID_HOST:-192.168.1.200}"
UNRAID_SHARE="${UNRAID_SHARE:-Misc}"
MOUNT_POINT="${MOUNT_POINT:-$HOME/mnt/unraid-imessage}"
LOCAL_EXPORT="${LOCAL_EXPORT:-$HOME/imessage-export}"
BACKUP_ROOT="$MOUNT_POINT/imessage-backup"
SERVER_URL="${SERVER_URL:-${SEARCH_API:-http://$UNRAID_HOST:8095}}"
SEARCH_API="${SEARCH_API:-$SERVER_URL}"
COPY_METHOD="${COPY_METHOD:-full}"  # full converts HEIC->JPEG, CAF->MP4 for browser playback
FORCE_FULL="${FORCE_FULL:-0}"

log() { printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }

report() {
  [[ -n "${BACKUP_RUN_ID:-}" && -n "${CLIENT_TOKEN:-}" ]] || return 0
  local status="${1:-}" phase="${2:-}" message="${3:-}"
  curl -fsS -X POST "$SERVER_URL/api/clients/backup/status" \
    -H "Authorization: Bearer $CLIENT_TOKEN" \
    -H 'Content-Type: application/json' \
    -d "{\"run_id\":\"$BACKUP_RUN_ID\",\"status\":\"$status\",\"phase\":\"$phase\",\"message\":\"$message\"}" \
    >/dev/null 2>&1 || true
}

ensure_mount() {
  mkdir -p "$MOUNT_POINT"
  if mount | grep -q " on $MOUNT_POINT "; then return 0; fi
  log "Mounting smb://$UNRAID_HOST/$UNRAID_SHARE"
  if [[ -n "${SMB_USER:-}" ]]; then
    mount_smbfs "//${SMB_USER}${SMB_PASS:+:$SMB_PASS}@$UNRAID_HOST/$UNRAID_SHARE" "$MOUNT_POINT"
  elif ! mount_smbfs "//guest@$UNRAID_HOST/$UNRAID_SHARE" "$MOUNT_POINT" 2>/dev/null; then
    mount_smbfs "//root@$UNRAID_HOST/$UNRAID_SHARE" "$MOUNT_POINT"
  fi
}

check_full_disk_access() {
  if ! imessage-exporter -d >/dev/null 2>&1; then
    log "ERROR: Full Disk Access required."
    log "  Add these in System Settings → Privacy & Security → Full Disk Access:"
    log "    • /usr/bin/python3  (background backup agent)"
    log "    • /opt/homebrew/bin/imessage-exporter"
    log "  Terminal also needs FDA if you run backups manually."
    open "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles" || true
    report "error" "preflight" "Full Disk Access required for /usr/bin/python3 and imessage-exporter"
    exit 1
  fi
}

read_checkpoint_rowid() {
  python3 - "$CHECKPOINT_FILE" <<'PY'
import json, sys
from pathlib import Path
p = Path(sys.argv[1])
if not p.exists():
    print(0)
    raise SystemExit
try:
    data = json.loads(p.read_text())
    print(int(data.get("last_message_rowid") or 0))
except Exception:
    print(0)
PY
}

write_checkpoint() {
  local rowid="$1"
  python3 - "$CHECKPOINT_FILE" "$rowid" <<'PY'
import json, sys
from datetime import datetime, timezone
from pathlib import Path
path = Path(sys.argv[1])
rowid = int(sys.argv[2])
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps({
    "last_message_rowid": rowid,
    "last_success_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "mode": "incremental",
}, indent=2) + "\n")
print(f"Checkpoint saved → {path} (rowid={rowid})")
PY
}

export_messages() {
  mkdir -p "$LOCAL_EXPORT"/{html,raw,logs}
  local logfile="$LOCAL_EXPORT/logs/$(date +%Y-%m-%d_%H%M%S).log"
  local since_rowid=0
  local mode="full"
  NEW_IDS_FILE=""
  EXPORT_MAX_ROWID=0

  if [[ "$FORCE_FULL" != "1" ]] && [[ -f "$LOCAL_EXPORT/messages.jsonl" ]]; then
    since_rowid="$(read_checkpoint_rowid)"
    if [[ "$since_rowid" -gt 0 ]]; then
      mode="incremental"
    fi
  fi

  report "running" "export" "Exporting messages ($mode)"
  osascript -e 'quit app "Messages"' 2>/dev/null || true
  sleep 2

  if [[ "$mode" == "full" ]]; then
    log "Full export — wiping local HTML and rebuilding"
    rm -rf "$LOCAL_EXPORT/html"
    mkdir -p "$LOCAL_EXPORT/html"
    log "Exporting HTML (copy-method=$COPY_METHOD)..."
    imessage-exporter -f html -c "$COPY_METHOD" -o "$LOCAL_EXPORT/html" 2>&1 | tee -a "$logfile"
  else
    log "Incremental export since ROWID $since_rowid — keeping existing HTML tree"
    mkdir -p "$LOCAL_EXPORT/html"
    # Refresh HTML for recent days so new attachment copies exist when not on Immich.
    local start_day
    start_day="$(date -v-2d +%Y-%m-%d 2>/dev/null || date -d '2 days ago' +%Y-%m-%d)"
    local staging="$LOCAL_EXPORT/html-staging-$$"
    rm -rf "$staging"
    mkdir -p "$staging"
    if imessage-exporter -f html -c "$COPY_METHOD" -s "$start_day" -o "$staging" 2>&1 | tee -a "$logfile"; then
      rsync -a "$staging/" "$LOCAL_EXPORT/html/" || true
    else
      log "WARN: incremental HTML exporter failed; continuing with raw attachments"
    fi
    rm -rf "$staging"
  fi

  report "running" "export" "Copying chat.db + Attachments"
  log "Copying raw database and attachments..."
  cp "$HOME/Library/Messages/chat.db" "$LOCAL_EXPORT/raw/"
  mkdir -p "$LOCAL_EXPORT/raw/Attachments"
  if [[ -t 1 ]] || [[ "${IMESSAGE_ARCHIVE_PROGRESS:-}" == "1" ]]; then
    rsync -a --partial --info=progress2 "$HOME/Library/Messages/Attachments/" "$LOCAL_EXPORT/raw/Attachments/"
  else
    rsync -a --partial --progress "$HOME/Library/Messages/Attachments/" "$LOCAL_EXPORT/raw/Attachments/"
  fi

  report "running" "export" "Exporting contacts"
  log "Exporting contacts..."
  python3 "$SCRIPT_DIR/export-contacts.py" --out "$LOCAL_EXPORT/contacts.json" 2>&1 | tee -a "$logfile" || true

  report "running" "export" "Building messages.jsonl ($mode)"
  log "Building JSONL ($mode)..."
  if [[ "$mode" == "incremental" ]]; then
    local delta="$LOCAL_EXPORT/messages.delta.jsonl"
    local export_log
    export_log="$(mktemp)"
    python3 "$SCRIPT_DIR/export-to-jsonl.py" \
      --db "$HOME/Library/Messages/chat.db" \
      --out "$delta" \
      --contacts "$LOCAL_EXPORT/contacts.json" \
      --html-dir "$LOCAL_EXPORT/html" \
      --since-rowid "$since_rowid" 2>&1 | tee -a "$logfile" | tee "$export_log"
    EXPORT_MAX_ROWID="$(awk -F= '/^MAX_ROWID=/{v=$2} END{print v+0}' "$export_log")"
    rm -f "$export_log"
    [[ "$EXPORT_MAX_ROWID" -gt 0 ]] || EXPORT_MAX_ROWID="$since_rowid"
    python3 "$SCRIPT_DIR/merge-jsonl.py" \
      --base "$LOCAL_EXPORT/messages.jsonl" \
      --delta "$delta" \
      --out "$LOCAL_EXPORT/messages.jsonl" 2>&1 | tee -a "$logfile"
    NEW_IDS_FILE="$LOCAL_EXPORT/messages.new-ids.txt"
  else
    local export_log
    export_log="$(mktemp)"
    python3 "$SCRIPT_DIR/export-to-jsonl.py" \
      --db "$HOME/Library/Messages/chat.db" \
      --out "$LOCAL_EXPORT/messages.jsonl" \
      --contacts "$LOCAL_EXPORT/contacts.json" \
      --html-dir "$LOCAL_EXPORT/html" 2>&1 | tee -a "$logfile" | tee "$export_log"
    EXPORT_MAX_ROWID="$(awk -F= '/^MAX_ROWID=/{v=$2} END{print v+0}' "$export_log")"
    rm -f "$export_log"
    if [[ "${EXPORT_MAX_ROWID:-0}" -eq 0 ]]; then
      EXPORT_MAX_ROWID="$(python3 -c "
import json
from pathlib import Path
mx=0
for line in Path('$LOCAL_EXPORT/messages.jsonl').open():
    m=json.loads(line)
    mx=max(mx, int(m.get('message_id') or 0))
print(mx)
")"
    fi
    NEW_IDS_FILE=""
  fi
  export NEW_IDS_FILE EXPORT_MAX_ROWID
}

upload_to_immich() {
  if [[ -z "${IMMICH_API_KEY:-}" ]]; then
    log "IMMICH_API_KEY not set — skipping Immich upload (will sync attachments locally)"
    return 0
  fi
  report "running" "immich" "Uploading media to Immich"
  log "Uploading photos/videos to Immich album '${IMMICH_ALBUM:-iMessage}'..."
  python3 "$SCRIPT_DIR/upload-to-immich.py" \
    --jsonl "$LOCAL_EXPORT/messages.jsonl" \
    --html-dir "$LOCAL_EXPORT/html" \
    --raw-dir "$LOCAL_EXPORT/raw" \
    --immich-url "${IMMICH_URL:-http://192.168.1.200:8090}" \
    --api-key "$IMMICH_API_KEY" \
    --album "${IMMICH_ALBUM:-iMessage}" \
    --map-file "$LOCAL_EXPORT/immich-map.json" 2>&1 | tee -a "$LOCAL_EXPORT/logs/immich-upload.log"
}

rsync_retry() {
  # SMB mounts occasionally drop mid-transfer; remount and resume up to 3 times.
  local attempt
  local -a opts=(-a --partial)
  if [[ -t 1 ]] || [[ "${IMESSAGE_ARCHIVE_PROGRESS:-}" == "1" ]]; then
    opts+=(--info=progress2)
  fi
  for attempt in 1 2 3; do
    if rsync "${opts[@]}" "$@"; then return 0; fi
    log "rsync failed (attempt $attempt), remounting and retrying..."
    umount "$MOUNT_POINT" 2>/dev/null || true
    sleep 5
    ensure_mount
  done
  rsync "${opts[@]}" "$@"
}

sync_to_server() {
  report "running" "sync" "Syncing to server"
  log "Syncing to server..."
  report "running" "sync" "Uploading messages.jsonl"
  rsync_retry "$LOCAL_EXPORT/messages.jsonl" "$BACKUP_ROOT/messages.jsonl"
  report "running" "sync" "Uploading contacts.json"
  rsync_retry "$LOCAL_EXPORT/contacts.json" "$BACKUP_ROOT/contacts.json" 2>/dev/null || true
  rsync_retry "$LOCAL_EXPORT/immich-map.json" "$BACKUP_ROOT/immich-map.json" 2>/dev/null || true
  if [[ -z "${IMMICH_API_KEY:-}" ]]; then
    log "No Immich key — syncing html + raw attachments locally"
    report "running" "sync" "Uploading html-export (may take a while)"
    if [[ "${FORCE_FULL:-0}" == "1" ]] || [[ ! -f "$CHECKPOINT_FILE" ]]; then
      rsync_retry --delete "$LOCAL_EXPORT/html/" "$BACKUP_ROOT/html-export/"
    else
      # Incremental: never delete server-only HTML paths
      rsync_retry "$LOCAL_EXPORT/html/" "$BACKUP_ROOT/html-export/"
    fi
    report "running" "sync" "Uploading raw attachments"
    rsync_retry "$LOCAL_EXPORT/raw/" "$BACKUP_ROOT/raw/"
  else
    log "Immich enabled — skipping bulk attachment rsync (media lives in Immich)"
  fi
}

trigger_reindex() {
  report "running" "index" "Reindexing search"
  if [[ -n "${NEW_IDS_FILE:-}" && -f "${NEW_IDS_FILE}" ]]; then
    local count
    count="$(wc -l < "$NEW_IDS_FILE" | tr -d ' ')"
    if [[ "$count" == "0" ]]; then
      log "No new message ids — skipping index"
      return 0
    fi
    log "Triggering incremental vector index ($count ids)..."
    python3 - "$SERVER_URL" "$NEW_IDS_FILE" <<'PY'
import json, sys, urllib.request
server, path = sys.argv[1], sys.argv[2]
ids = [line.strip() for line in open(path) if line.strip()]
body = json.dumps({"full": False, "ids": ids}).encode()
req = urllib.request.Request(f"{server.rstrip('/')}/api/index", data=body, method="POST")
req.add_header("Content-Type", "application/json")
with urllib.request.urlopen(req, timeout=60) as resp:
    print(resp.read().decode())
PY
  else
    log "Triggering full vector reindex..."
    curl -fsS -X POST "$SERVER_URL/api/index" -H 'Content-Type: application/json' \
      -d '{"full": true}' >/dev/null || log "WARN: reindex request failed"
  fi
}

main() {
  if ! command -v imessage-exporter >/dev/null; then
    log "ERROR: imessage-exporter not found (PATH=$PATH)"
    report "error" "preflight" "imessage-exporter not found — install with: brew install imessage-exporter"
    exit 1
  fi
  mkdir -p "$LOCAL_EXPORT"
  ensure_mount
  check_full_disk_access
  export_messages
  upload_to_immich
  sync_to_server
  trigger_reindex
  if [[ "${EXPORT_MAX_ROWID:-0}" -gt 0 ]]; then
    write_checkpoint "$EXPORT_MAX_ROWID"
  fi
  report "success" "done" "Backup complete"
  log "Done. View at $SERVER_URL"
}

main "$@"
