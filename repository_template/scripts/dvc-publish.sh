#!/usr/bin/env bash
set -euo pipefail

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "error: publication must run inside a Git repository" >&2
  exit 2
fi

dirty_metadata="$(git status --porcelain -- dvc.yaml dvc.lock '*.dvc')"
if [[ -n "$dirty_metadata" ]]; then
  echo "error: commit all relevant DVC metadata before publication" >&2
  printf '%s\n' "$dirty_metadata" >&2
  exit 3
fi

commit_sha="$(git rev-parse HEAD)"
upstream_sha="$(git rev-parse '@{upstream}' 2>/dev/null || true)"
if [[ -z "$upstream_sha" || "$upstream_sha" != "$commit_sha" ]]; then
  echo "error: the current commit must be pushed to its configured upstream" >&2
  exit 4
fi

exec homebrew-mlflow publication submit --commit-sha "$commit_sha" "$@"
