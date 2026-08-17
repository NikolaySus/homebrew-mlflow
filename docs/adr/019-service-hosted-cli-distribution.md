# ADR-019: Service-hosted CLI distribution

Status: accepted
Date: 2026-08-13

## Decision

Each Homebrew MLflow deployment is authoritative for compatible `homebrew-mlflow` CLI
releases. The same HTTPS origin exposes an unauthenticated Python Simple Repository containing
the CLI and a curated, locked wheelhouse for every supported Python/OS target. Public PyPI is
not a publication target or dependency fallback.

The server advertises an exact recommended version and a compatibility constraint. Installation
commands pin the recommendation and use the service as the default index. Package possession is
not authorization; all authorization remains server-side.

## Consequences

- A user can bootstrap the helper before authenticating.
- Deployments retain compatible and rollback CLI artifacts as immutable, hashed release inputs.
- Release CI must build and test the complete cross-platform wheelhouse.
- The CLI remains instance-neutral; `login --server` records the selected deployment URL.
