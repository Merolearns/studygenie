#!/usr/bin/env bash
# StudyGenie restore — from a backup file onto an ISOLATED target.
#
# Usage:
#   ./scripts/restore.sh <backup-file> <target-db-path>
#
# The target must NOT be the live database path of a running app.
# Procedure for a real restore: stop the app, restore to the live path,
# start the app, verify /healthz and login.
set -euo pipefail

BACKUP="${1:?usage: restore.sh <backup-file> <target-db-path>}"
TARGET="${2:?usage: restore.sh <backup-file> <target-db-path>}"

if [[ ! -f "$BACKUP" ]]; then
  echo "error: backup file not found: $BACKUP" >&2
  exit 1
fi
if [[ -e "$TARGET" ]]; then
  echo "error: refusing to overwrite existing file: $TARGET" >&2
  echo "remove it explicitly first if you really mean to replace it." >&2
  exit 1
fi

cp "$BACKUP" "$TARGET"
if ! sqlite3 "$TARGET" "PRAGMA integrity_check;" | grep -q "^ok$"; then
  echo "error: restored database failed integrity check" >&2
  rm -f "$TARGET"
  exit 1
fi
echo "restore OK: $TARGET"
