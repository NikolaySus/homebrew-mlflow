# Tested compatibility matrix

The v1 compatibility unit is pinned and supported only where exercised by automated or
recorded acceptance tests.

| Component | Version / range |
| --- | --- |
| Python | 3.11–3.13 |
| PostgreSQL | 16 |
| DVC client | 3.67.1 |
| MLflow server and client | 3.15.1 |
| GitLab CE | 19.2.1-ce.0 |
| Infisical | 0.162.14 |
| MinIO | Compose image digest in `deploy/compose/compose.yaml` |
| Researcher OS | Windows, macOS, Linux |

The supported initial MLflow subset is Run lookup/resume, parameter logging, scalar metric
logging/history, tag upsert, batch logging, coordinator-owned termination, and policy-limited Run
attachments. Experiment/Run deletion, restoration, model registry, model binary logging, tracing,
and job execution return or surface `unsupported_operation` and are not supported.
