#!/usr/bin/env bash
# Export iMessages from Mac, sync to server, trigger vector reindex.
set -euo pipefail

# launchd agents get a minimal PATH — include Homebrew and user bins.
export PATH="/opt/homebrew/bin:/usr/local/bin:${HOME}/bin:/usr/bin:/bin:/usr/sbin:/sbin"

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
COPY_METHOD="${COPY_METHOD:-full}"  # full converts HEIC->JPEG, CAF->MP4 for browser playback

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

export_messages() {
  mkdir -p "$LOCAL_EXPORT"/{html,raw,logs}
  local logfile="$LOCAL_EXPORT/logs/$(date +%Y-%m-%d_%H%M%S).log"
  report "running" "export" "Exporting messages (HTML)"

  osascript -e 'quit app "Messages"' 2>/dev/null || true
  sleep 2

  log "Exporting HTML (copy-method=$COPY_METHOD)..."
  rm -rf "$LOCAL_EXPORT/html"
  mkdir -p "$LOCAL_EXPORT/html"
  imessage-exporter -f html -c "$COPY_METHOD" -o "$LOCAL_EXPORT/html" 2>&1 | tee -a "$logfile"

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

  report "running" "export" "Building messages.jsonl"
  log "Building JSONL..."
  python3 "$SCRIPT_DIR/export-to-jsonl.py" \
    --db "$HOME/Library/Messages/chat.db" \
    --out "$LOCAL_EXPORT/messages.jsonl" \
    --contacts "$LOCAL_EXPORT/contacts.json" \
    --html-dir "$LOCAL_EXPORT/html" 2>&1 | tee -a "$logfile"
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
  # --info=progress2 gives an overall % when run from a TTY (CLI backup).
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
    rsync_retry --delete "$LOCAL_EXPORT/html/" "$BACKUP_ROOT/html-export/"
    report "running" "sync" "Uploading raw attachments"
    rsync_retry "$LOCAL_EXPORT/raw/" "$BACKUP_ROOT/raw/"
  else
    log "Immich enabled — skipping bulk attachment rsync (media lives in Immich)"
  fi
}

trigger_reindex() {
  report "running" "index" "Reindexing search"
  log "Triggering vector reindex..."
  curl -fsS -X POST "$SERVER_URL/api/index" -H 'Content-Type: application/json' \
    -d '{"full": true}' >/dev/null || log "WARN: reindex request failed"
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
  report "success" "done" "Backup complete"
  log "Done. View at $SERVER_URL"
}

main "$@"
