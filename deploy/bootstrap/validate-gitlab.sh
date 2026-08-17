#!/bin/sh
set -eu

token="$(cat /run/platform-secrets/gitlab-integration-token)"
gitlab_host="${HOMEBREW_MLFLOW_GITLAB_HOST:-git.localhost}"
status="$(curl --silent --show-error --output /dev/null --write-out '%{http_code}' \
  --header "Host: ${gitlab_host}" \
  --header "PRIVATE-TOKEN: ${token}" http://127.0.0.1/api/v4/user)"
if test "${status}" != "200"; then
  echo "GitLab integration token validation returned HTTP ${status}" >&2
  exit 1
fi
echo "GitLab integration token authenticated"
