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
SEARCH_API="${SEARCH_API:-http://$UNRAID_HOST:8095}"
COPY_METHOD="${COPY_METHOD:-clone}"  # clone | full | disabled

log() { printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }

ensure_mount() {
  mkdir -p "$MOUNT_POINT"
  if mount | grep -q " on $MOUNT_POINT "; then
    return 0
  fi
  log "Mounting smb://$UNRAID_HOST/$UNRAID_SHARE -> $MOUNT_POINT"
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
    exit 1
  fi
}

export_messages() {
  mkdir -p "$LOCAL_EXPORT"/{html,raw,logs}
  local logfile="$LOCAL_EXPORT/logs/$(date +%Y-%m-%d_%H%M%S).log"

  log "Quitting Messages app to avoid DB lock..."
  osascript -e 'quit app "Messages"' 2>/dev/null || true
  sleep 2

  log "Exporting HTML archive (copy-method=$COPY_METHOD)..."
  imessage-exporter -f html -c "$COPY_METHOD" -o "$LOCAL_EXPORT/html" 2>&1 | tee -a "$logfile"

  log "Copying raw database..."
  cp "$HOME/Library/Messages/chat.db" "$LOCAL_EXPORT/raw/"
  rsync -a "$HOME/Library/Messages/Attachments/" "$LOCAL_EXPORT/raw/Attachments/" 2>/dev/null || true

  log "Building JSONL for vector search..."
  python3 "$SCRIPT_DIR/export-to-jsonl.py" \
    --db "$HOME/Library/Messages/chat.db" \
    --out "$LOCAL_EXPORT/messages.jsonl" 2>&1 | tee -a "$logfile"
}

sync_to_server() {
  log "Syncing to server..."
  rsync -av --delete "$LOCAL_EXPORT/html/" "$BACKUP_ROOT/html-export/"
  rsync -av "$LOCAL_EXPORT/raw/" "$BACKUP_ROOT/raw/"
  rsync -av "$LOCAL_EXPORT/messages.jsonl" "$BACKUP_ROOT/messages.jsonl"
}

trigger_reindex() {
  log "Triggering vector reindex..."
  if curl -fsS -X POST "$SEARCH_API/index" -H 'Content-Type: application/json' \
      -d '{"full": true}' >/dev/null; then
    log "Reindex started."
  else
    log "WARN: Could not reach search API at $SEARCH_API"
  fi
}

main() {
  command -v imessage-exporter >/dev/null || { log "Run: scripts/install-client.sh"; exit 1; }
  mkdir -p "$LOCAL_EXPORT"
  ensure_mount
  check_full_disk_access
  export_messages
  sync_to_server
  trigger_reindex
  log "Done."
  log "  HTML browse: $BACKUP_ROOT/html-export/"
  log "  Search UI:   $SEARCH_API"
}

main "$@"
