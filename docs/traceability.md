# V1 requirement traceability

This table links the implementation specification's acceptance scenarios to executable evidence.
`Boundary` means a real local process, PostgreSQL, S3-compatible API, or HTTP application boundary;
`contract` and `unit` checks supplement those boundaries. A production restore drill remains deployment
evidence and cannot be inferred from a unit test.

| Scenario | Primary implementation | Automated evidence | Level |
| --- | --- | --- | --- |
| GitLab login, project membership, repository seed, doctor | bootstrapped OAuth/PAT, project provisioning, `repository_template`, CLI | `test_web_oauth_security.py`, `test_gitlab_oauth.py`, `test_repository_template.py`, `test_project_provisioning_coordinator.py`, `scripts/compose_acceptance.py` | boundary/HTTP/unit |
| Local Run with central metrics | Run coordinator and tracking store/plugin | `test_run_service.py`, `test_tracking_service.py`, `test_mlflow_tracking_store.py` | unit/subprocess |
| Long Run credential refresh | rotating refresh families and coordinator-owned child context | `test_refresh_tokens.py`, `test_access_tokens.py`, `test_cli_run.py` | unit/subprocess |
| Infisical injection without secret capture | official bootstrap/CLI, file-backed reconciliation credential, redaction | `test_cli_run.py`, `test_redaction.py`, `scripts/compose_acceptance.py` | boundary/subprocess/unit |
| Native DVC push does not publish | separate STS credential and publication APIs | `test_dvc_credentials.py`, `test_cli_dvc_credentials.py`, `test_publication_service.py` | unit/contract |
| File/directory publication and atomic catalog creation | publication queue, isolated validator, work store | `test_publication_validator.py`, `test_publication_coordinator.py`, `test_publication_service.py` | boundary/unit |
| SSE replay, reconnect, expiry, idempotency | durable publication events and cursor replay | `test_publication_service.py`, `test_api_contract.py` | unit/contract |
| Viewer/Contributor/Maintainer policy | application authorization, Admin recovery, scoped and expiring machine credentials | `test_authorization.py`, `test_membership_service.py`, `test_machine_credentials.py` | unit |
| Exact-version sharing and prospective revocation | grants, references, completed-Run recovery | `test_sharing_service.py`, `test_sharing_persistence.py`, `test_artifact_catalog_recovery.py` | SQL/unit |
| Consuming-project derivation | immutable derivation edge | `test_sharing_service.py`, `test_sharing_persistence.py` | SQL/unit |
| DVC credential expiry and least privilege | 15-minute STS policy, no delete, exact shared keys | `test_dvc_credentials.py`, `test_cli_dvc_credentials.py`, `test_artifact_catalog_recovery.py` | SQL/unit |
| `mlflow.end_run()` never publishes DVC outputs | tracking/publication boundary | `test_mlflow_tracking_store.py`, `test_publication_service.py` | unit |
| Read-only complete Swagger contract | canonical OpenAPI and disabled submit methods | `test_api_contract.py` | HTTP/contract |
| Archive-first retention dependency graph | project/repository/experiment/pipeline/artifact archive state and blockers | `test_project_service.py`, `test_repository_service.py`, `test_run_service.py`, `test_retention.py`; SQL blocker integration is part of the Compose acceptance profile | unit |
| Backup restoration | quiesced backup/restore scripts, provider-binding secret archive, runbook | `test_backup_scripts.py`; quarterly isolated drill in `docs/operations/backup-and-restore.md` | contract/deployment gate |

Before a release is called archive-ready, CI must pass the repository checks in `AGENTS.md`, the
Compose acceptance profile must exercise GitLab, Infisical, PostgreSQL, MinIO, API, MLflow and both
workers, and the latest quarterly restore drill must have a recorded successful result.
Run its public-boundary smoke gate with `uv run python scripts/compose_acceptance.py` after
`docker compose -f deploy/compose/compose.yaml up -d --build`.
The gate also checks migration head `0022`, both workers, generated OAuth coordinates, authenticated
GitLab integration access, the Infisical credential file, and Prometheus rule syntax.
