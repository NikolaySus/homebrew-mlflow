# Platform test notes

These notes track reproducible platform or generated-workflow problems found while using this repository.
User command mistakes and problems caused solely by an outdated local CLI are intentionally excluded.

## 2026-08-18

### Successful DVC push emits a credential-refresh traceback

**State:** Deployed in CLI 0.2.3; awaiting a real DVC transfer verification.

An approved five-object `dvc push` completed and returned exit code zero, but afterward aiobotocore attempted
an advisory credential refresh and printed a full `CredentialRetrievalError` traceback caused by a TLS
handshake timeout in the credential helper. A subsequent `dvc status -c -r platform` confirmed that cache
and remote were fully synchronized.

Expected: advisory refresh should retry or fail quietly after a completed transfer, and normal transient
network errors should be summarized without a multi-page traceback that makes a successful upload appear
failed.

Verification: the helper now retries connection and TLS-establishment failures three times with bounded
backoff, does not replay ambiguous read or HTTP failures, and emits a redacted one-line terminal error.
Unit tests cover recovery, exhaustion, and non-retry behavior. CLI 0.2.3 installs successfully from the
production index. A real DVC transfer remains required before marking this resolved.

### Direct MLflow logging is rejected by the platform proxy

**State:** Deployed; public Host verification passed, awaiting managed Run logging verification.

During Run `run_01M0B1DTPGEHFW5YAA6ANR73PA`, the local DVC stage completed its computation but
`mlflow.log_metrics` received HTTP 403 from `/mlflow/api/2.0/mlflow/runs/get` with `Invalid Host header -
possible DNS rebinding attack detected`. The platform then correctly retained the Run as failed.

Expected: the public tracking URL configured by `homebrew-mlflow run` is accepted by MLflow through the
reverse proxy, including the Host/forwarded-host combination. DVC-native metrics remain the workaround for
this repository and are not duplicated through the fluent MLflow API.

Verification: the rebuilt MLflow image accepts `ml.spkya.ru` on a tracking API path while rejecting an
unlisted host with HTTP 403. Production now routes the formerly rejected API path into MLflow without an
`Invalid Host` response, and the production Compose acceptance probe passes. A managed Run remains required
before marking this resolved.

### Service index omits a required repository dependency

**State:** Resolved in the production package index.

The generated `pyproject.toml` requires `homebrew-mlflow-plugins==0.1.0` from the explicit platform index,
but `https://ml.spkya.ru/packages/simple/homebrew-mlflow-plugins/` returns HTTP 404. Existing frozen
environments continue to work only when the wheel is already available from `uv.lock` and the local cache;
`uv add` cannot resolve the project.

Expected: every package referenced by the current repository template and lock remains available from the
service-hosted simple index, and a clean machine can run `uv sync --frozen` and author dependency changes.

Verification: the reproducible plugin wheel matches the hash pinned by the repository template, the index
builder publishes its project page and now rejects an incomplete first-party wheelhouse, and a rendered
template completed a cache-disabled frozen sync against the locally served index. The production project
page now returns HTTP 200, and `homebrew-mlflow-plugins==0.1.0` installs from that index on a clean Python
environment.

### Generated DVC credentials cannot access the configured remote

**State:** Resolved by CLI/configuration v3 in 0.2.1.

After `homebrew-mlflow repository configure` and a successful `homebrew-mlflow doctor`, the generated
AWS profile is found and its `credential_process` is invoked by the repository-pinned DVC 3.67.1.
Nevertheless, the configured remote rejects a metadata request:

```text
uv run --frozen dvc pull -r platform -v
ERROR: failed to connect to s3 (research/dvc/files/md5)
403 Forbidden during HeadObject
```

Expected: credentials issued for this repository can read and write the repository's permitted DVC
object prefix.

Likely fix area: verify that the temporary credential policy and the generated DVC remote path refer to
the same bucket/prefix and allow the metadata operations DVC/s3fs performs.

Verification: after `homebrew-mlflow repository configure`, the remote includes the project-specific
prefix. Both `homebrew-mlflow doctor` and repository-pinned DVC can access it without the former HTTP 403;
an authenticated `dvc push` successfully transferred two starter output objects.

### `doctor` reports DVC OK without validating remote access

**State:** Resolved in CLI 0.2.1.

`homebrew-mlflow doctor` reports `git=ok` and `dvc=ok` even though the generated DVC configuration receives
HTTP 403 from object storage immediately afterward.

Expected: repository readiness checking should safely validate credential-process execution and a
non-mutating operation against the configured project prefix, or clearly report that `dvc=ok` checks only
the local executable/configuration and does not verify remote access.

Verification: `homebrew-mlflow doctor` now reports separate `dvc_configuration=ok`, `dvc_remote=ok`, and
`readiness=ok` results.

### First DVC use makes a clean generated repository dirty

**State:** Resolved by repository configuration v3.

The initial repository contains `.dvc/config` but omits the normal `.dvc/.gitignore`. When DVC first creates
its local cache state, it creates `.dvc/.gitignore`; that file is not ignored by the root `.gitignore`. The
next Run therefore fails before executing its command:

```text
RuntimeError: repository has uncommitted changes
```

Expected: normal setup and DVC inspection should leave a freshly cloned repository clean. Include the
standard `.dvc/.gitignore` in the repository template or ignore the generated local file at the root.

Verification: `homebrew-mlflow repository configure` added `.dvc/.gitignore` as tracked configuration; after
committing the v3 migration, DVC inspection leaves the worktree clean.

### Run provenance rejects normal DVC experiment changes

**State:** Resolved in CLI 0.2.2 and repository template v4.

The Run wrapper starts successfully and its child `dvc exp run` completes, creates `dvc.lock`, produces the
declared outputs, records the metric, and creates a named DVC experiment. During finalization, the wrapper
calls its clean/upstream Git check again. Because the successful DVC command changed expected workspace
metadata, the wrapper sets a provenance error and converts the zero child exit into a failed Run.

Observed result:

```text
Run run_01M0ASY5GC0XDSQ2YTE04ZY3MM started.
Ran experiment(s): initial-baseline
Run run_01M0ASY5GC0XDSQ2YTE04ZY3MM finalized as failed.
```

The underlying DVC experiment revision is `f917fa8`, and its generated metric is `starter=1.0`.

Expected: require a clean, pushed input commit before execution, but allow and capture the DVC metadata and
declared outputs produced by the command. The post-run check should verify that `HEAD` did not change without
treating expected DVC workspace changes as an automatic command failure; unexpected source changes should
still be reported separately.

Verification: retry Run `run_01M0B03AE93222XNYGF3B9D6CA` finalized as succeeded without a provenance
warning. It captured named DVC experiment revision `ab1ff88e2a465297ac6c704c4b95940e72a65bd8`, retained
`starter=1.0`, and left the Git worktree and DVC pipeline clean.

### Generated uv instructions can execute a global DVC on Windows

**State:** Resolved by repository template v4 guidance.

The generated README runs `uv sync --frozen` and then shows plain `dvc` commands. On Windows, `uv sync`
does not activate `.venv`; plain `dvc` can therefore select a global installation. In this test it selected
global DVC 3.59.1 instead of the repository-pinned DVC 3.67.1 and failed inside the S3 credential subprocess
path with `NotImplementedError`.

Expected: generated commands should be environment-independent. Use `uv run --frozen dvc ...` for standalone
DVC commands; inside `homebrew-mlflow run`, keep the child as `dvc ...` and let the Run helper supply the
declared uv environment.

Verification: template v4 uses `uv run --frozen dvc ...` for standalone DVC commands and explicitly keeps
the child command as plain `dvc ...` inside `homebrew-mlflow run`, where the Run helper supplies the selected
uv environment itself.
