#!/usr/bin/env bash
# Mount the Unraid SMB share for iMessage backups.
set -euo pipefail

CONFIG_FILE="${CONFIG_FILE:-$HOME/.config/imessage-archive.env}"
if [[ -f "$CONFIG_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$CONFIG_FILE"
fi

UNRAID_HOST="${UNRAID_HOST:-192.168.1.200}"
UNRAID_SHARE="${UNRAID_SHARE:-Misc}"
MOUNT_POINT="${MOUNT_POINT:-$HOME/mnt/unraid-imessage}"

mkdir -p "$MOUNT_POINT"
if mount | grep -q " on $MOUNT_POINT "; then
  echo "Already mounted: $MOUNT_POINT"
  exit 0
fi

if [[ -n "${SMB_USER:-}" ]]; then
  mount_smbfs "//${SMB_USER}${SMB_PASS:+:$SMB_PASS}@$UNRAID_HOST/$UNRAID_SHARE" "$MOUNT_POINT"
elif mount_smbfs "//guest@$UNRAID_HOST/$UNRAID_SHARE" "$MOUNT_POINT" 2>/dev/null; then
  :
else
  mount_smbfs "//root@$UNRAID_HOST/$UNRAID_SHARE" "$MOUNT_POINT"
fi

echo "Mounted: //$UNRAID_HOST/$UNRAID_SHARE -> $MOUNT_POINT"
