#!/usr/bin/env bash
# StudyGenie backup — WAL-safe consistent snapshot of the SQLite database.
#
# Uses sqlite3's online backup API (via the .backup dot-command), which
# copies a transactionally consistent state INCLUDING uncheckpointed WAL
# contents. Never copy the bare .db file while the app is writing.
#
# Usage:
#   ./scripts/backup.sh [output-dir]     # -> <output-dir>/studygenie-YYYYMMDD-HHMMSS.db
#   DB_PATH=/data/studygenie.db ./scripts/backup.sh /backups
#
# In Docker: docker compose exec app /app/scripts/backup.sh /data/backups
set -euo pipefail

DB_PATH="${DB_PATH:-${STUDYGENIE_DB:-/data/studygenie.db}}"
OUT_DIR="${1:-./backups}"

if [[ ! -f "$DB_PATH" ]]; then
  echo "error: database not found at $DB_PATH" >&2
  exit 1
fi

mkdir -p "$OUT_DIR"
STAMP="$(date +%Y%m%d-%H%M%S)"
DEST="$OUT_DIR/studygenie-$STAMP.db"

sqlite3 "$DB_PATH" ".backup '$DEST'"

# verify the backup opens and passes an integrity check
if ! sqlite3 "$DEST" "PRAGMA integrity_check;" | grep -q "^ok$"; then
  echo "error: backup integrity check failed for $DEST" >&2
  rm -f "$DEST"
  exit 1
fi

echo "backup OK: $DEST ($(du -h "$DEST" | cut -f1))"
