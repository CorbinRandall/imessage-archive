#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/CorbinRandall/imessage-archive.git}"
INSTALL_DIR="${INSTALL_DIR:-$HOME/.local/imessage-archive}"
CONFIG_DIR="$HOME/.config"
CONFIG_FILE="$CONFIG_DIR/imessage-archive.env"
BIN_DIR="$HOME/bin"
PYTHON="$(command -v python3)"

echo "==> Installing iMessage Archive client"

if ! command -v brew >/dev/null; then
  echo "ERROR: Homebrew required."
  exit 1
fi

if ! command -v imessage-exporter >/dev/null; then
  echo "==> Installing imessage-exporter..."
  brew install imessage-exporter
fi

echo "==> Cloning/updating repo to $INSTALL_DIR"
if [[ -d "$INSTALL_DIR/.git" ]]; then
  git -C "$INSTALL_DIR" pull --ff-only
else
  git clone "$REPO_URL" "$INSTALL_DIR"
fi

mkdir -p "$CONFIG_DIR" "$BIN_DIR" "$HOME/mnt" "$HOME/imessage-export"

if [[ ! -f "$CONFIG_FILE" ]]; then
  cp "$INSTALL_DIR/config/env.example" "$CONFIG_FILE"
  echo "Created $CONFIG_FILE"
fi

chmod +x "$INSTALL_DIR/client/"*.sh "$INSTALL_DIR/client/"*.py

ln -sf "$INSTALL_DIR/client/export-and-sync.sh" "$BIN_DIR/imessage-backup"
ln -sf "$INSTALL_DIR/client/mount-share.sh" "$BIN_DIR/imessage-mount"

# Background agent (server-driven schedules + manual triggers)
PLIST_DST="$HOME/Library/LaunchAgents/com.imessage-archive.agent.plist"
sed -e "s|HOME|$HOME|g" -e "s|PYTHON|$PYTHON|g" \
  "$INSTALL_DIR/client/com.imessage-archive.agent.plist" > "$PLIST_DST"
launchctl unload "$PLIST_DST" 2>/dev/null || true
launchctl load "$PLIST_DST"

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
echo "  • /opt/homebrew/bin/imessage-exporter"
echo "  • Terminal (if running manual backups)"
echo ""
echo "Then open: http://192.168.1.200:8095"
