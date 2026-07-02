#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${REPO_DIR:-/mnt/user/appdata/imessage-archive}"
ENV_FILE="${ENV_FILE:-$REPO_DIR/.env}"

if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=source=/dev/null
  source "$ENV_FILE"
fi

APP_PATH="${APP_PATH:-$REPO_DIR}"
DATA_PATH="${DATA_PATH:-/mnt/user/Misc/imessage-backup}"
SEARCH_PORT="${SEARCH_PORT:-8095}"

export APP_PATH DATA_PATH SEARCH_PORT

mkdir -p "$DATA_PATH"/{html-export,raw,logs} "$APP_PATH"/{qdrant,state}
chmod -R 777 "$DATA_PATH" 2>/dev/null || true

echo "Building image..."
docker build --network=host -t imessage-archive:latest "$APP_PATH/server"

echo "Starting containers..."
docker compose -f "$APP_PATH/server/docker-compose.yml" up -d

# Migrate old container name if present
docker rm -f imessage-search 2>/dev/null || true

HOST="${SERVER_HOST:-$(hostname -I | awk '{print $1}')}"
echo ""
echo "iMessage Archive is running."
echo "  Web UI:     http://${HOST}:${SEARCH_PORT}"
echo "  Data path:  $DATA_PATH"
echo "  State DB:   $APP_PATH/state"
