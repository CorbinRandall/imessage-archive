#!/usr/bin/env bash
# Install iMessage archive client on macOS.
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/CorbinRandall/imessage-archive.git}"
INSTALL_DIR="${INSTALL_DIR:-$HOME/.local/imessage-archive}"
CONFIG_DIR="$HOME/.config"
CONFIG_FILE="$CONFIG_DIR/imessage-archive.env"
BIN_DIR="$HOME/bin"

echo "==> Installing iMessage Archive client"

if ! command -v brew >/dev/null; then
  echo "ERROR: Homebrew required. Install from https://brew.sh"
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
  echo "Created config: $CONFIG_FILE (edit UNRAID_HOST if needed)"
fi

ln -sf "$INSTALL_DIR/client/export-and-sync.sh" "$BIN_DIR/imessage-backup"
ln -sf "$INSTALL_DIR/client/mount-share.sh" "$BIN_DIR/imessage-mount"
chmod +x "$INSTALL_DIR/client/"*.sh "$INSTALL_DIR/client/"*.py

PLIST_SRC="$INSTALL_DIR/client/com.imessage-archive.backup.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.imessage-archive.backup.plist"
sed "s|HOME|$HOME|g" "$PLIST_SRC" > "$PLIST_DST"
launchctl unload "$PLIST_DST" 2>/dev/null || true
launchctl load "$PLIST_DST"

echo ""
echo "Client installed."
echo "  Config:     $CONFIG_FILE"
echo "  Backup:     imessage-backup"
echo "  Mount:      imessage-mount"
echo ""
echo "Before first run, grant Full Disk Access to Terminal:"
echo "  System Settings > Privacy & Security > Full Disk Access"
echo ""
echo "Then run:  imessage-backup"
