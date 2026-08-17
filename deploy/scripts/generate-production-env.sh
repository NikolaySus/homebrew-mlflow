#!/bin/sh
set -eu

destination=${1:-/opt/homebrew-mlflow-secrets/production.env}
if [ -s "$destination" ]; then
  echo "Production environment already exists"
  exit 0
fi

umask 077
temporary="${destination}.tmp"
{
  printf 'PLATFORM_DB_PASSWORD=%s\n' "$(openssl rand -hex 24)"
  printf 'PLATFORM_SIGNING_KEY=%s\n' "$(openssl rand -hex 48)"
  printf '%s\n' 'PLATFORM_SIGNING_KEY_ID=production-v1'
  printf 'PLATFORM_BOOTSTRAP_TOKEN=%s\n' "$(openssl rand -hex 32)"
  printf 'MINIO_ROOT_USER=%s\n' "$(openssl rand -hex 10)"
  printf 'MINIO_ROOT_PASSWORD=%s\n' "$(openssl rand -hex 32)"
  printf 'INFISICAL_ENCRYPTION_KEY=%s\n' "$(openssl rand -hex 16)"
  printf 'INFISICAL_AUTH_SECRET=%s\n' "$(openssl rand -hex 32)"
  printf 'INFISICAL_DB_PASSWORD=%s\n' "$(openssl rand -hex 24)"
  printf 'INFISICAL_ADMIN_PASSWORD=%s\n' "$(openssl rand -hex 24)"
  printf 'GRAFANA_ADMIN_PASSWORD=%s\n' "$(openssl rand -hex 24)"
} > "$temporary"
chmod 0600 "$temporary"
mv "$temporary" "$destination"
echo "Production environment generated"
