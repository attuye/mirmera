#!/usr/bin/env bash
# Nightly backup of Mirmera's data: a consistent copy of the database (SQLite's online backup,
# safe while Mirmera runs) and the evidence folder, kept here for BACKUP_KEEP_DAYS days and,
# when BACKUP_S3_* is set in deploy/.env, copied to S3-compatible storage.
# secret.key / MIRMERA_SECRET_KEY is NOT in the backup on purpose: keep it apart.
#
# Run from cron as root (see deploy/README.md):
#   15 2 * * * /srv/mirmera/deploy/backup.sh >> /var/log/mirmera-backup.log 2>&1
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
set -a; source "$HERE/.env"; set +a
DATA="${MIRMERA_DATA:-/srv/mirmera-data}"
OUT="$DATA/backups"
STAMP="$(date +%F-%H%M)"
COMPOSE=(docker compose -f "$HERE/docker-compose.yml")

mkdir -p "$OUT" && chown 10001:10001 "$OUT"
"${COMPOSE[@]}" exec -T app python -c "
import sqlite3
src = sqlite3.connect('/data/mirmera.db')
dst = sqlite3.connect('/data/backups/mirmera-$STAMP.db')
src.backup(dst)
dst.close()
"
gzip -f "$OUT/mirmera-$STAMP.db"
echo "$(date -Is) database: $OUT/mirmera-$STAMP.db.gz"

if [[ -n "${BACKUP_S3_BUCKET:-}" ]]; then
  aws() {
    docker run --rm -v "$DATA:/data:ro" -e AWS_ACCESS_KEY_ID="$BACKUP_S3_ACCESS_KEY" \
      -e AWS_SECRET_ACCESS_KEY="$BACKUP_S3_SECRET_KEY" -e AWS_DEFAULT_REGION=us-east-1 \
      amazon/aws-cli --endpoint-url "$BACKUP_S3_ENDPOINT" "$@"
  }
  aws s3 cp "/data/backups/mirmera-$STAMP.db.gz" "s3://$BACKUP_S3_BUCKET/db/" --only-show-errors
  aws s3 sync /data/evidence "s3://$BACKUP_S3_BUCKET/evidence/" --only-show-errors  # only new files
  echo "$(date -Is) copied to s3://$BACKUP_S3_BUCKET"
fi

find "$OUT" -name 'mirmera-*.db.gz' -mtime +"${BACKUP_KEEP_DAYS:-14}" -delete
