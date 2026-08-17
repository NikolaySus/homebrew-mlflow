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

exec homebrew-mlflow publication submit "$@"
