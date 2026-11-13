#!/usr/bin/env bash
# Copy the live message store, owner corrections and tickets from the EC2 server (consistent
# SQLite backups, read only on the server) into DEST. Usage: pull_data.sh DEST_DIR
set -euo pipefail
DEST="${1:?usage: pull_data.sh DEST_DIR}"
KEY="${AMIRCLAW_SSH_KEY:-$HOME/Downloads/amirclaw-key.pem}"
HOST="${AMIRCLAW_HOST:-ubuntu@16.171.33.170}"
DBS="whatsapp_messages reports_corrections tickets"

mkdir -p "$DEST"
ssh -o ConnectTimeout=15 -i "$KEY" "$HOST" "cd ~/amirclaw/workspace && for d in $DBS; do sqlite3 \$d.db \".backup /tmp/kwpull_\$d.db\"; done"
for d in $DBS; do
    scp -q -i "$KEY" "$HOST:/tmp/kwpull_$d.db" "$DEST/$d.db"
done
ssh -i "$KEY" "$HOST" "rm -f /tmp/kwpull_*.db"
# The engine reads the SQLite store only once the Excel backfill is marked done
sqlite3 "$DEST/whatsapp_messages.db" "INSERT OR REPLACE INTO meta (key, value) VALUES ('excel_backfill_done', '1')" 2>/dev/null || true
ls -la "$DEST"
echo "Messages: $(sqlite3 "$DEST/whatsapp_messages.db" 'select count(*) from messages'), newest: $(sqlite3 "$DEST/whatsapp_messages.db" 'select timestamp_text from messages order by ts_utc_ms desc limit 1')"
