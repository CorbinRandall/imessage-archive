#!/usr/bin/env bash
# Deploy or update the iMessage archive stack on Unraid.
set -euo pipefail

REPO_DIR="${REPO_DIR:-/mnt/user/appdata/imessage-archive}"
ENV_FILE="${ENV_FILE:-$REPO_DIR/.env}"

if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$ENV_FILE"
fi

APP_PATH="${APP_PATH:-$REPO_DIR}"
DATA_PATH="${DATA_PATH:-/mnt/user/Misc/imessage-backup}"
SEARCH_PORT="${SEARCH_PORT:-8095}"

export APP_PATH DATA_PATH SEARCH_PORT

mkdir -p "$DATA_PATH"/{html-export,raw,logs} "$APP_PATH/qdrant"

echo "Building search image..."
docker build --network=host -t imessage-search:latest "$APP_PATH/server"

echo "Starting containers..."
docker compose -f "$APP_PATH/server/docker-compose.yml" up -d

echo ""
echo "iMessage Archive is running."
echo "  Search UI:  http://${SERVER_HOST:-$(hostname -I | awk '{print $1}')}:$SEARCH_PORT"
echo "  Data path:  $DATA_PATH"
echo "  Health:     curl http://localhost:$SEARCH_PORT/health"
