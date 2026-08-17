# Homebrew MLflow

Self-hosted research system of record for ML experiments, provenance, and immutable DVC-backed
artifacts. Research computation remains on researcher-controlled machines.

## Development

```powershell
uv sync --all-packages
uv run pytest
uv run homebrew-mlflow-api
```

The API listens on `http://localhost:8000`. The complete development edge is available through
`docker compose -f deploy/compose/compose.yaml up --build` after building the web application and
package index.

Build the current-platform wheelhouse and static index with:

```powershell
uv build --package homebrew-mlflow --out-dir build/wheels
uv build --package homebrew-mlflow-contracts --out-dir build/wheels
uv export --package homebrew-mlflow --no-dev --no-emit-workspace --output-file build/cli-requirements.txt
uv run --with pip python -m pip download --require-hashes --only-binary=:all: -r build/cli-requirements.txt -d build/wheels
uv run python scripts/build_package_index.py build/wheels build/packages
```

The CLI is distributed by each service deployment, not public PyPI. See
`docs/adr/019-service-hosted-cli-distribution.md`.

## Provider bootstrap

Compose automatically provisions the local GitLab OAuth application, a 90-day GitLab integration
token, and the Infisical admin identity. One-shot `gitlab-bootstrap` and `infisical-bootstrap`
services write their results to the `platform-secrets` named volume; API and worker containers mount
that volume read-only and load the values through `*_FILE` settings. Generated values are never
printed or placed in container environment variables. Repeated starts are idempotent.

The checked-in administrator password values are development-only. A production override must supply
unique Infisical bootstrap credentials, HTTPS public URLs, platform signing/bootstrap keys, database
credentials, and object-store credentials before first start. Back up the `platform-secrets` volume
under the same encrypted, off-host policy as the provider databases. If a provider database is already
initialized but its corresponding secret file is absent, bootstrap fails closed and requires an
operator-led credential recovery instead of silently replacing identity state.

## AI agent guidance

Platform-development agents follow the repository-root `AGENTS.md`. Platform-created research repositories
include `repository_template/AGENTS.md`, which lets expert-directed agents run reproducible local
experiments and manage experiment branches while protecting credentials, shared history, and the explicit
publication boundary.

## Research repository bootstrap

The checked-in `repository_template/` is the source for new GitLab research repositories. Repository
provisioning renders deployment-specific platform, DVC, and object-store values and commits the complete
seed through GitLab. The template includes agent guidance, credential-aware DVC/AWS configuration,
cross-platform publication scripts, a local Run coordinator, and a safe MLflow autologging example.

Create research projects through the platform web UI or CLI rather than GitLab's project wizard:

```powershell
homebrew-mlflow project create --name "Protein Folding" --clone-to .\protein-folding
homebrew-mlflow project status protein-folding
```

Creation provisions one private, seeded default repository asynchronously. The CLI waits for that
repository by default; use `--no-wait` for automation and `project retry` after a reported provisioning
failure. GitLab-native templates do not create the platform's project, authorization, storage, or
provenance records and are therefore not a supported creation boundary.
