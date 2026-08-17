# Instructions for AI agents

## Scope and authority

This file applies to the entire `homebrew-mlflow` platform repository. The product contract is
`homebrew-mlflow-implementation-specification.md`; read the relevant sections before changing behavior.
If code, documentation, and the specification disagree, do not silently choose one: preserve working
behavior, identify the mismatch, and update the specification when the requested change alters the
contract.

`repository_template/AGENTS.md` is a product asset copied into research repositories. Its instructions
govern agents working on researchers' experiments; they do not authorize experiments in this platform
repository.

## Product boundaries

- This service is an archival control plane for Git/DVC/MLflow metadata and artifacts.
- Training, evaluation, `dvc repro`, and `dvc exp run` happen on researcher-controlled machines. Never
  add server-side experiment execution.
- Git and DVC remain visible, native tools. Do not replace them with a proprietary workflow or make
  `dvc push` mean publication.
- Publication is an explicit, immutable registration step. The server derives and verifies identity;
  clients must not supply trusted hashes or storage keys.
- The browser authenticates with an HTTP-only session cookie. CLI and SDK clients use scoped tokens.
- Artifact-store credentials are short-lived and obtained through the credential helper. Never persist,
  print, or return long-lived storage credentials.

## Working in this repository

1. Read this file, the relevant specification sections, and any more specific `AGENTS.md` before editing.
2. Inspect `git status` and nearby code first. Preserve user changes and unrelated work.
3. For a non-trivial change, state a short plan and implement it through a verifiable milestone.
4. Keep changes narrow. Do not perform opportunistic rewrites or dependency upgrades.
5. Use non-destructive Git operations. Never discard changes, rewrite shared history, force-push, or
   delete branches/tags unless the user explicitly requests the exact operation.
6. Do not commit, push, open pull requests, or modify external systems unless the user asks for it.

## Implementation rules

- Keep authorization and lifecycle invariants in domain/application code, not only in HTTP handlers.
- Apply tenant and project scoping to every data and object-store access path.
- Treat published Runs, DVC states, and artifact manifests as immutable.
- Add an Alembic migration for every persistent schema change. Do not edit an applied migration to
  represent a new schema state.
- Keep the OpenAPI document and generated clients synchronized with API contract changes.
- Extend the official CLI/SDK rather than adding one-off shell protocols.
- Keep Bash and PowerShell research-repository workflows behaviorally equivalent.
- Use real boundary tests for PostgreSQL, S3-compatible storage, and subprocess behavior where those
  boundaries matter. Mocks may supplement these tests, not replace them.
- Never add secrets, private keys, tokens, `.env` contents, research data, DVC cache contents, or generated
  artifact blobs to Git.

## Verification

Run the smallest relevant checks while iterating, then broaden them in proportion to the change. Standard
commands are:

```text
uv sync --all-packages
uv run ruff check .
uv run mypy -p homebrew_mlflow.domain -p homebrew_mlflow.application -p homebrew_mlflow.contracts -p homebrew_mlflow.infrastructure -p homebrew_mlflow.cli
uv run pytest
npm ci --prefix apps/web
npm run build --prefix apps/web
docker compose -f deploy/compose/compose.yaml config
docker compose -f deploy/compose/compose.yaml up --build
```

For migrations, verify upgrade from a populated previous schema as well as a clean database. For Docker
changes, wait for health checks and exercise the affected endpoint rather than treating container startup
as sufficient verification. If a check cannot run, report the exact command and reason.

## Handoff

Summarize the user-visible result, files or contracts changed, verification performed, and any remaining
risk. Include paths and actionable errors. Do not claim a test, publication, upload, or deployment occurred
unless it was observed directly.
