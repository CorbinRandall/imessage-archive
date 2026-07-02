#!/usr/bin/env bash
# Export iMessages from Mac, sync to server, trigger vector reindex.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="${CONFIG_FILE:-$HOME/.config/imessage-archive.env}"

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
COPY_METHOD="${COPY_METHOD:-clone}"

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
    log "ERROR: Full Disk Access required for Terminal."
    open "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles" || true
    report "error" "preflight" "Full Disk Access required"
    exit 1
  fi
}

export_messages() {
  mkdir -p "$LOCAL_EXPORT"/{html,raw,logs}
  local logfile="$LOCAL_EXPORT/logs/$(date +%Y-%m-%d_%H%M%S).log"
  report "running" "export" "Exporting messages"

  osascript -e 'quit app "Messages"' 2>/dev/null || true
  sleep 2

  log "Exporting HTML (copy-method=$COPY_METHOD)..."
  imessage-exporter -f html -c "$COPY_METHOD" -o "$LOCAL_EXPORT/html" 2>&1 | tee -a "$logfile"

  log "Copying raw database and attachments..."
  cp "$HOME/Library/Messages/chat.db" "$LOCAL_EXPORT/raw/"
  rsync -a "$HOME/Library/Messages/Attachments/" "$LOCAL_EXPORT/raw/Attachments/" 2>/dev/null || true

  log "Building JSONL..."
  python3 "$SCRIPT_DIR/export-to-jsonl.py" \
    --db "$HOME/Library/Messages/chat.db" \
    --out "$LOCAL_EXPORT/messages.jsonl" 2>&1 | tee -a "$logfile"
}

sync_to_server() {
  report "running" "sync" "Syncing to server"
  log "Syncing to server..."
  rsync -av --delete "$LOCAL_EXPORT/html/" "$BACKUP_ROOT/html-export/"
  rsync -av "$LOCAL_EXPORT/raw/" "$BACKUP_ROOT/raw/"
  rsync -av "$LOCAL_EXPORT/messages.jsonl" "$BACKUP_ROOT/messages.jsonl"
}

trigger_reindex() {
  report "running" "index" "Reindexing search"
  log "Triggering vector reindex..."
  curl -fsS -X POST "$SERVER_URL/api/index" -H 'Content-Type: application/json' \
    -d '{"full": true}' >/dev/null || log "WARN: reindex request failed"
}

main() {
  command -v imessage-exporter >/dev/null || { report "error" "preflight" "imessage-exporter not installed"; exit 1; }
  mkdir -p "$LOCAL_EXPORT"
  ensure_mount
  check_full_disk_access
  export_messages
  sync_to_server
  trigger_reindex
  report "success" "done" "Backup complete"
  log "Done. View at $SERVER_URL"
}

main "$@"
