#!/bin/bash
# task-board backup — runs daily, keeps 7 days
set -euo pipefail

DB="/home/exedev/task-board/instance/task_board.db"
BACKUP_DIR="/home/exedev/task-board/backups"
DATE=$(date +%Y-%m-%d_%H%M)

mkdir -p "$BACKUP_DIR"

# Only backup if DB exists and WAL checkpoint is clean
if [ -f "$DB" ]; then
    # Checkpoint WAL to main DB before backup
    sqlite3 "$DB" "PRAGMA wal_checkpoint(TRUNCATE);" 2>/dev/null || true
    # Copy DB
    cp "$DB" "$BACKUP_DIR/task_board_${DATE}.db"
    # Also backup WAL if it exists
    [ -f "${DB}-wal" ] && cp "${DB}-wal" "$BACKUP_DIR/task_board_${DATE}.db-wal"
    # Compress old backups
    find "$BACKUP_DIR" -name "task_board_*.db" -mtime +1 -exec gzip {} \; 2>/dev/null || true
    # Remove backups older than 7 days
    find "$BACKUP_DIR" -name "task_board_*.db*" -mtime +7 -delete 2>/dev/null || true
    echo "Backup: task_board_${DATE}.db"
else
    echo "ERROR: DB not found at $DB"
    exit 1
fi
