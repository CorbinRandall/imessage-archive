#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/CorbinRandall/imessage-archive.git}"
BRANCH="${BRANCH:-main}"
INSTALL_DIR="${INSTALL_DIR:-$HOME/.local/imessage-archive}"
CONFIG_DIR="$HOME/.config"
CONFIG_FILE="$CONFIG_DIR/imessage-archive.env"
BIN_DIR="$HOME/bin"
PYTHON="$(command -v python3)"

echo "==> Installing iMessage Archive client (branch: $BRANCH)"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "ERROR: This installer must run on a Mac (detected $(uname -s))."
  echo "Open Terminal on the Mac and run the dashboard install command there."
  exit 1
fi

if ! command -v brew >/dev/null; then
  echo "ERROR: Homebrew required. Install from https://brew.sh then re-run."
  exit 1
fi

if ! command -v imessage-exporter >/dev/null; then
  echo "==> Installing imessage-exporter..."
  brew install imessage-exporter
fi

echo "==> Cloning/updating repo to $INSTALL_DIR"
if [[ -d "$INSTALL_DIR/.git" ]]; then
  git -C "$INSTALL_DIR" fetch --quiet origin
  git -C "$INSTALL_DIR" checkout "$BRANCH"
  git -C "$INSTALL_DIR" pull --ff-only origin "$BRANCH"
else
  git clone --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
fi

mkdir -p "$CONFIG_DIR" "$BIN_DIR" "$HOME/mnt" "$HOME/imessage-export"

if [[ ! -f "$CONFIG_FILE" ]]; then
  cp "$INSTALL_DIR/config/env.example" "$CONFIG_FILE"
  echo "Created $CONFIG_FILE"
fi

# Honor SERVER_URL / SEARCH_API / UNRAID_HOST if the parent install-mac.sh exported them.
set_kv() {
  local k="$1" v="$2"
  [[ -n "$v" ]] || return 0
  if grep -q "^${k}=" "$CONFIG_FILE" 2>/dev/null; then
    sed -i.bak "s|^${k}=.*|${k}=${v}|" "$CONFIG_FILE" && rm -f "$CONFIG_FILE.bak"
  else
    printf '%s=%s\n' "$k" "$v" >> "$CONFIG_FILE"
  fi
}
set_kv SERVER_URL "${SERVER_URL:-}"
set_kv SEARCH_API "${SEARCH_API:-${SERVER_URL:-}}"
set_kv UNRAID_HOST "${UNRAID_HOST:-}"
set_kv UNRAID_SHARE "${UNRAID_SHARE:-Misc}"

chmod +x "$INSTALL_DIR/client/"*.sh "$INSTALL_DIR/client/"*.py 2>/dev/null || true

ln -sf "$INSTALL_DIR/client/export-and-sync.sh" "$BIN_DIR/imessage-backup"
ln -sf "$INSTALL_DIR/client/mount-share.sh" "$BIN_DIR/imessage-mount"
if [[ -f "$INSTALL_DIR/client/cli.py" ]]; then
  ln -sf "$INSTALL_DIR/client/cli.py" "$BIN_DIR/imessage-archive"
fi

# Background agent (server-driven schedules + manual triggers)
PLIST_DST="$HOME/Library/LaunchAgents/com.imessage-archive.agent.plist"
sed -e "s|HOME|$HOME|g" -e "s|PYTHON|$PYTHON|g" \
  "$INSTALL_DIR/client/com.imessage-archive.agent.plist" > "$PLIST_DST"
launchctl unload "$PLIST_DST" 2>/dev/null || true
launchctl load "$PLIST_DST"
launchctl kickstart -k "gui/$(id -u)/com.imessage-archive.agent" 2>/dev/null || true

# Remove legacy monthly-only job if present
launchctl unload "$HOME/Library/LaunchAgents/com.imessage-archive.backup.plist" 2>/dev/null || true
launchctl unload "$HOME/Library/LaunchAgents/com.corbin.imessage-backup.plist" 2>/dev/null || true

echo ""
echo "Client installed."
echo "  Config:  $CONFIG_FILE"
echo "  Agent:   running via launchd (polls server for schedules)"
echo "  Manual:  imessage-backup"
echo ""
echo "Required — Full Disk Access (System Settings → Privacy & Security):"
echo "  • /usr/bin/python3"
echo "  • $(command -v imessage-exporter)"
echo "  • Terminal (only if running manual backups)"
echo ""
SERVER="$(grep -E '^SERVER_URL=' "$CONFIG_FILE" 2>/dev/null | head -1 | cut -d= -f2-)"
echo "Then open: ${SERVER:-http://192.168.1.200:8095}"
open "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles" 2>/dev/null || true
