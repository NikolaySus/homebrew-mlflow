#!/bin/sh
set -eu

if [ "${1:-}" != "--confirm-empty-target" ]; then
  echo "Refusing restore: pass --confirm-empty-target after verifying isolated empty targets." >&2
  exit 2
fi
: "${RESTORE_ID:?set RESTORE_ID to a completed backup timestamp}"
: "${BACKUP_S3_ENDPOINT:?set BACKUP_S3_ENDPOINT}"
: "${BACKUP_S3_BUCKET:?set BACKUP_S3_BUCKET}"
: "${BACKUP_S3_ACCESS_KEY:?set BACKUP_S3_ACCESS_KEY}"
: "${BACKUP_S3_SECRET_KEY:?set BACKUP_S3_SECRET_KEY}"

compose_file=${COMPOSE_FILE:-deploy/compose/compose.yaml}
work=$(mktemp -d)
trap 'rm -rf -- "$work"' EXIT INT TERM
mc alias set backup "$BACKUP_S3_ENDPOINT" "$BACKUP_S3_ACCESS_KEY" "$BACKUP_S3_SECRET_KEY"
mc cp --recursive \
  "backup/$BACKUP_S3_BUCKET/$RESTORE_ID/control-plane/" "$work/"

docker compose -f "$compose_file" stop gateway api publication-worker-1 \
  publication-worker-2 infisical
docker compose -f "$compose_file" exec -T gitlab gitlab-ctl stop puma
docker compose -f "$compose_file" exec -T gitlab gitlab-ctl stop sidekiq

docker compose -f "$compose_file" exec -T postgres \
  pg_restore -U homebrew_mlflow -d homebrew_mlflow --clean --if-exists \
  < "$work/platform.pgdump"
docker compose -f "$compose_file" exec -T infisical-db \
  pg_restore -U infisical -d infisical --clean --if-exists \
  < "$work/infisical.pgdump"
docker compose -f "$compose_file" cp "$work/gitlab.tar" \
  "gitlab:/var/opt/gitlab/backups/${RESTORE_ID}_gitlab_backup.tar"
docker compose -f "$compose_file" cp "$work/gitlab-config.tar.gz" \
  "gitlab:/tmp/gitlab-config.tar.gz"
docker compose -f "$compose_file" cp "$work/platform-secrets.tar.gz" \
  "gitlab:/tmp/platform-secrets.tar.gz"
docker compose -f "$compose_file" exec -T gitlab \
  tar -C /etc/gitlab -xzf /tmp/gitlab-config.tar.gz
docker compose -f "$compose_file" exec -T gitlab \
  tar -C /run/platform-secrets -xzf /tmp/platform-secrets.tar.gz
docker compose -f "$compose_file" exec -T gitlab gitlab-ctl reconfigure
docker compose -f "$compose_file" exec -T gitlab gitlab-ctl stop puma
docker compose -f "$compose_file" exec -T gitlab gitlab-ctl stop sidekiq
docker compose -f "$compose_file" exec -T gitlab \
  chown git:git "/var/opt/gitlab/backups/${RESTORE_ID}_gitlab_backup.tar"
docker compose -f "$compose_file" exec -T gitlab \
  gitlab-backup restore BACKUP="$RESTORE_ID" force=yes

mc alias set target "${TARGET_S3_ENDPOINT:-http://localhost:9000}" \
  "${TARGET_S3_ACCESS_KEY:-development}" \
  "${TARGET_S3_SECRET_KEY:-development-only-password}"
mc mirror --overwrite \
  "backup/$BACKUP_S3_BUCKET/$RESTORE_ID/objects/research" target/research
mc mirror --overwrite \
  "backup/$BACKUP_S3_BUCKET/$RESTORE_ID/objects/homebrew-mlflow" target/homebrew-mlflow
docker compose -f "$compose_file" exec -T gitlab gitlab-ctl restart
docker compose -f "$compose_file" exec -T gitlab gitlab-rake gitlab:check SANITIZE=true
docker compose -f "$compose_file" start infisical api publication-worker-1 \
  publication-worker-2 gateway
echo "Restore $RESTORE_ID completed; run the acceptance and integrity checks before promotion."
