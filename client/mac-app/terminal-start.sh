#!/bin/bash
# Run from Terminal after the .app installs files.
set +e
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/bin:/usr/bin:/bin:/usr/sbin:/sbin"
clear
echo "========================================"
echo "  iMessage Archive"
echo "========================================"
echo

CLI="$HOME/.local/imessage-archive/client/cli.py"
if [[ ! -f "$CLI" ]]; then
  echo "Client not installed. Re-download the Mac app from the dashboard."
  echo
  read -r -p "Press return to close… "
  exit 1
fi

SERVER_URL="${SERVER_URL:-}"
if [[ -z "$SERVER_URL" && -f "$HOME/.config/imessage-archive.env" ]]; then
  # shellcheck disable=SC1090
  source "$HOME/.config/imessage-archive.env"
fi

python3 "$CLI" setup ${SERVER_URL:+--server "$SERVER_URL"}
echo
echo "Status refreshes live.  Ctrl+C to stop watching."
echo "Then run:  imessage-archive backup"
echo
python3 "$CLI" watch
echo
read -r -p "Press return to close… "
