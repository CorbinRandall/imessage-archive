#!/usr/bin/env bash
# One-command server setup: clone repo on Unraid and deploy.
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/CorbinRandall/imessage-archive.git}"
APP_PATH="${APP_PATH:-/mnt/user/appdata/imessage-archive}"

echo "==> Installing iMessage Archive server to $APP_PATH"

if [[ -d "$APP_PATH/.git" ]]; then
  echo "==> Updating existing repo..."
  git -C "$APP_PATH" pull --ff-only
else
  git clone "$REPO_URL" "$APP_PATH"
fi

if [[ ! -f "$APP_PATH/.env" ]]; then
  cp "$APP_PATH/config/env.example" "$APP_PATH/.env"
  echo "Created $APP_PATH/.env — edit SERVER_HOST / paths if needed"
fi

chmod +x "$APP_PATH/scripts/"*.sh
"$APP_PATH/scripts/install-server.sh"
