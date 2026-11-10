#!/bin/sh
# Daily PostgreSQL dump with retention. Restore: gunzip -c FILE | psql "$DATABASE_URL"
set -eu
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
OUT="/backups/engine-${STAMP}.sql.gz"
pg_dump --no-owner --format=plain | gzip -9 > "${OUT}.tmp"
mv "${OUT}.tmp" "${OUT}"
find /backups -name 'engine-*.sql.gz' -mtime +"${BACKUP_RETENTION_DAYS:-14}" -delete
echo "backup written: ${OUT}"
