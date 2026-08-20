# Tested compatibility matrix

The v1 compatibility unit is pinned and supported only where exercised by automated or
recorded acceptance tests.

| Component | Version / range |
| --- | --- |
| Python | 3.11–3.13 |
| PostgreSQL | 16 |
| DVC client | 3.67.1 |
| MLflow server and client | 3.15.1 |
| Homebrew MLflow CLI | 0.2.9 (`>=0.2.9,<0.3`) |
| Homebrew MLflow server plugin | 0.1.6 |
| Research-repository MLflow plugin | 0.1.6 |
| GitLab CE | 19.2.1-ce.0 |
| Infisical | 0.162.14 |
| MinIO | Compose image digest in `deploy/compose/compose.yaml` |
| Researcher OS | Windows, macOS, Linux |

The supported client-write subset is Run lookup/resume, parameter logging, scalar metric
logging/history, tag upsert, batch logging, coordinator-owned termination, and policy-limited Run
attachments. The read-only browser subset adds native project Workspaces, Experiment/Run search
and detail, parameters, metrics and histories, tags, attachment list/download, DVC-backed dataset
inputs, and read-only Logged Model/Model Registry views with immutable interface schemas and Artifact
Version provenance. Experiment/Run mutation, registry mutation,
model binary logging, tracing, prompts, GenAI evaluation, and job execution return or surface
`unsupported_operation` and are not supported.
