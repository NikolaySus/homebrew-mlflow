#!/bin/sh
set -eu

: "${BACKUP_S3_ENDPOINT:?set BACKUP_S3_ENDPOINT}"
: "${BACKUP_S3_BUCKET:?set BACKUP_S3_BUCKET}"
: "${BACKUP_S3_ACCESS_KEY:?set BACKUP_S3_ACCESS_KEY}"
: "${BACKUP_S3_SECRET_KEY:?set BACKUP_S3_SECRET_KEY}"

compose_file=${COMPOSE_FILE:-deploy/compose/compose.yaml}
stamp=$(date -u +%Y%m%dT%H%M%SZ)
work=$(mktemp -d)
cleanup() {
  docker compose -f "$compose_file" exec -T gitlab gitlab-ctl start puma >/dev/null 2>&1 || true
  docker compose -f "$compose_file" exec -T gitlab gitlab-ctl start sidekiq >/dev/null 2>&1 || true
  docker compose -f "$compose_file" start infisical api publication-worker-1 \
    publication-worker-2 gateway >/dev/null 2>&1 || true
  rm -rf -- "$work"
}
trap cleanup EXIT INT TERM

docker compose -f "$compose_file" stop gateway api publication-worker-1 \
  publication-worker-2 infisical
docker compose -f "$compose_file" exec -T gitlab gitlab-ctl stop puma
docker compose -f "$compose_file" exec -T gitlab gitlab-ctl stop sidekiq

docker compose -f "$compose_file" exec -T postgres \
  pg_dump -U homebrew_mlflow -Fc homebrew_mlflow > "$work/platform.pgdump"
docker compose -f "$compose_file" exec -T infisical-db \
  pg_dump -U infisical -Fc infisical > "$work/infisical.pgdump"
docker compose -f "$compose_file" exec -T gitlab \
  gitlab-backup create BACKUP="$stamp" CRON=1
docker compose -f "$compose_file" cp \
  "gitlab:/var/opt/gitlab/backups/${stamp}_gitlab_backup.tar" "$work/gitlab.tar"
docker compose -f "$compose_file" exec -T gitlab \
  tar -C /etc/gitlab -czf - gitlab.rb gitlab-secrets.json > "$work/gitlab-config.tar.gz"
docker compose -f "$compose_file" exec -T gitlab \
  tar -C /run/platform-secrets -czf - . > "$work/platform-secrets.tar.gz"

mc alias set backup "$BACKUP_S3_ENDPOINT" "$BACKUP_S3_ACCESS_KEY" "$BACKUP_S3_SECRET_KEY"
mc alias set source "${SOURCE_S3_ENDPOINT:-http://localhost:9000}" \
  "${SOURCE_S3_ACCESS_KEY:-development}" \
  "${SOURCE_S3_SECRET_KEY:-development-only-password}"
mc mirror --overwrite source/research "backup/$BACKUP_S3_BUCKET/$stamp/objects/research"
mc mirror --overwrite source/homebrew-mlflow \
  "backup/$BACKUP_S3_BUCKET/$stamp/objects/homebrew-mlflow"
mc cp --recursive "$work/" "backup/$BACKUP_S3_BUCKET/$stamp/control-plane/"

printf '%s\n' "$stamp" | mc pipe "backup/$BACKUP_S3_BUCKET/latest-complete"
echo "Backup $stamp completed and marked restorable."
