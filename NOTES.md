# Platform test notes

These notes track reproducible platform or generated-workflow problems found while using this repository.
User command mistakes and problems caused solely by an outdated local CLI are intentionally excluded.

## 2026-08-19

### Publication worker fails after complete object validation

**State:** Resolved by platform commit `13a29dc` and worker redeployment.

Publication operation `pub_01M0D6D48V2M0WJZNVAG3GM3EC` resolved the committed top-five submission,
verified its complete 6,637,542-byte DVC object, entered `operation.committing`, and then failed with the
safe `worker_failed` code. PostgreSQL recorded a foreign-key violation because the worker attempted to
insert `artifact_storage_locations` before SQLAlchemy had flushed the parent `artifact_versions` row.

The finalizer now flushes the immutable Artifact Version before inserting its file-index and storage child
rows. A foreign-key-enforced integration regression test and the publication-focused suite pass. Both VPS
publication workers were rebuilt and recreated. Retry operation `pub_01M0D6TSXPJ7H8NTHVCJRXG5RB`
published successfully, followed by a complete catalog synchronization: 62 published Artifact Versions
across 10 families, with no partial version from the failed operation.

### Doctor reports MLflow authorization boundary failure

**State:** Open; does not block Git, DVC, managed CLI authentication, or publication.

After the publication-worker repair, repeated `homebrew-mlflow doctor` checks report
`mlflow_service=failed status=401` and `mlflow_auth_boundary=failed`, while `platform_session`,
`mlflow_client_auth`, `dvc_remote`, and publication remain functional. Expected: the readiness probe should
receive the accepted MLflow service response and report `readiness=ok`. This should be diagnosed separately
from the successfully repaired publication transaction.

### Managed Run finalization can fail after a successful DVC experiment

**State:** Resolved in CLI 0.2.6.

Run `run_01M0C7RWTXKGB3ZJ04Z7WK1Y0K` completed the unchanged CUDA CatBoost training plus diagnostic
outputs, and DVC created named experiment revision `ab7f7531f783a2cde30a7b5e02a418d03dfa95bd` with hashes
for all seven declared outputs. DVC separately emitted post-executor missing-file/hash warnings, which are
not evidence of incomplete platform provenance. The client then failed with HTTP 404 from
`/api/v1/runs/run_01M0C7RWTXKGB3ZJ04Z7WK1Y0K/finalize`; that failed API request, rather than the DVC warning,
is the issue recorded here. The command returned exit code one even though the DVC revision and all local
cache objects remained intact.

Expected: the finalize endpoint should accept an existing active Run, resolve provenance from the completed
named DVC experiment revision, and return a durable succeeded or failed response. A missing Run should be
diagnosed before the 36-minute child process executes, or finalization should recover without losing the
completed experiment association.

Reproduction: Run `run_01M0CBRRCHZ64BD0ZNR2DDK3AH` completed a 49-minute hurdle CatBoost stage and DVC
created revision `ec21bfba0195632f3b530c0930ee7c8466cd823a` with all seven output hashes. Finalization again
followed DVC's independent post-executor warnings, then failed during its API request with `ConnectError:
[Errno 11001] getaddrinfo failed`. Two pre-run `doctor` attempts had also timed out before a third reported
`readiness=ok`. The completed DVC state survived, but the client has no retry or resumable finalization path
for transient control-plane failures after expensive local work. No claim is made that the DVC warnings
caused the API failure or imply incorrect stored provenance.

CLI 0.2.6 adds Run-token heartbeats resilient to transient DNS, connection, timeout, 429, and server errors;
approximately two minutes of idempotent finalization retries; and a credential-free local finalization
journal written before the network request. If retries are exhausted, `homebrew-mlflow run-recover <RUN_ID>`
can finalize the already-computed worker-marked `INCOMPLETE` Run after connectivity returns, without
reopening the Run or rerunning computation. Recovery is limited to the Run creator or a project Maintainer.
The historical CLI 0.2.5 failures have no recovery journals and cannot verify this fix retroactively.

Local readiness check: CLI 0.2.6 is installed and recommended, and `homebrew-mlflow doctor` reports
`readiness=ok`. The resolution criterion was a new managed Run that either finalized normally through the
new path or was successfully finalized with `run-recover` after a reproduced connectivity interruption.

Verification: new Run `run_01M0CK007TJZX9AAXTS287EW6W` executed a cheap clean-state command under CLI
0.2.6 and finalized normally as `succeeded`. Journal-based `run-recover` remains intentionally untested
because this verification did not reproduce a connectivity interruption.

### DVC `--temp` emits missing-file/hash warnings after executor cleanup

**State:** Non-critical upstream DVC UX observation; not a demonstrated platform defect.

Successful managed Runs that execute `dvc exp run --temp` create complete named DVC experiment revisions,
but DVC later warns that declared outputs cannot be found at paths under the already-removed
`.dvc/tmp/exps/standalone/...` executor checkout. This reproduced for base Run
`run_01M0BNSS0M3YH5KRBERT6WV566`, global Run
`run_01M0BNT8W6V5DRG668BJ8KA1ND`, and correlation Run `run_01M0BQADP4R40BXVG0XMDC7SK1`.

Platform verification: all three Runs finalized successfully with `provenance_status=complete`, their exact
DVC revisions, committed `dvc.lock` evidence, and declared output paths. The platform reads `dvc.lock` from
the exact experiment revision with Git and does not derive provenance from the temporary executor checkout.
Therefore the warnings are retained only as noisy upstream DVC behavior. Reclassify this as a platform bug
only if a finalized Run can be shown to contain a missing or incorrect revision, lockfile, output path, or
publication identity; suppressing DVC warnings alone is not a platform fix.

### Repository v4 to v5 migration cannot match the generated lockfile

**State:** Resolved in CLI 0.2.5.

`homebrew-mlflow repository configure` rejects an otherwise unmodified template-v4 `uv.lock` with
`repository template migration conflicts with researcher changes in uv.lock`. The migration implementation
looks for literal `{{ platform_url }}` fragments, while an installed repository correctly contains its rendered
service URL (`https://ml.spkya.ru`). It therefore cannot match the managed package block or requirement that it
is intended to update.

Expected: migration matching should use the repository's configured platform URL (or compare the managed
fields structurally), update `homebrew-mlflow-plugins` from 0.1.0 to 0.1.1, and advance the template sentinel
from v4 to v5 without rejecting untouched generated content.

Workaround verification: the exact package version, wheel URL, published wheel hash, locked requirement, and
template sentinel changes intended by CLI 0.2.4 were applied manually. `uv lock --check` and
`uv sync --frozen` succeeded, and `homebrew-mlflow doctor` then reported `mlflow_client_auth=ok`,
`mlflow_auth_boundary=ok`, `dvc_remote=ok`, and `readiness=ok`.

Fix verification: CLI 0.2.5 reads the rendered service URL from `.homebrew-mlflow.json` and uses it when
matching both managed `uv.lock` fragments. An isolated in-memory migration using the exact former
`https://ml.spkya.ru` v4 package block and requirement upgraded the package version, wheel URL, wheel hash,
and locked requirement successfully. The live v5 repository continues to report `readiness=ok`.

### Successful Runs can also emit DVC missing-output warnings

**State:** Historical non-critical DVC UX observation; no platform provenance defect demonstrated.

Run `run_01M0BC5MBAN7F91E88Q3VBXZKS` completed its DVC experiment, updated `dvc.lock`, and finalized as
succeeded. Immediately afterward, the wrapper warned that no file hash information was found for each of
the four declared outputs. The files exist, `dvc status` reports the pipeline up to date, and DVC experiment
revision `cb9b7433d815b2a6830ecda135b36cffccde021a` contains their hashes and metrics.

Interpretation: the warning is not evidence that the platform failed to resolve provenance. Platform
provenance must be assessed from the finalized Run's stored revision, committed `dvc.lock` evidence, output
paths, and publication identity rather than from whether DVC can revisit an executor workspace path.

Recheck: final Run `run_01M0BCTCZ6YKTCXB3KWTQKBANE` recomputed the same stage and finalized without the
warnings. A later, otherwise successful Run `run_01M0BE7RMA3X9RKWP4NEQX5CWA` reproduced the warning for
all four declared outputs. DVC revision `d091d8127ec2d41739b37672e94375ff3808272c` contains their hashes,
and both local status and the remote transfer are checked separately.

Verification: Run `run_01M0BHZ0A0DBH75PWB61GNEEN2` forced the unchanged full DVC stage under CLI 0.2.4,
created named DVC experiment revision `dca4a7010f6c1d9ce1a07a8227ec1e7000d16125`, and finalized as
succeeded without any missing-hash warning. All four output hashes were present, `dvc status` reported the
pipeline up to date, and `dvc status -c -r platform` reported the cache and remote in sync. This comparison
only characterizes warning reproducibility; it does not establish a platform provenance failure in the Runs
that emitted the warning.

## 2026-08-18

### Successful DVC push emits a credential-refresh traceback

**State:** Resolved by CLI 0.2.5.

An approved five-object `dvc push` completed and returned exit code zero, but afterward aiobotocore attempted
an advisory credential refresh and printed a full `CredentialRetrievalError` traceback caused by a TLS
handshake timeout in the credential helper. A subsequent `dvc status -c -r platform` confirmed that cache
and remote were fully synchronized.

Expected: advisory refresh should retry or fail quietly after a completed transfer, and normal transient
network errors should be summarized without a multi-page traceback that makes a successful upload appear
failed.

Verification: an approved named-experiment transfer under CLI 0.2.5 uploaded 27 files in 48.9 seconds with
`uv run --frozen dvc exp push origin catboost-base-cuda-final catboost-global-trend-cuda
catboost-user-correlation-cuda -r platform -j 2`. The command exited zero without a credential-refresh
traceback. All three experiment refs were present on `origin` at their expected immutable DVC revisions.

### Direct MLflow tracking is unusable from a managed Run

**State:** Resolved by CLI 0.2.4 and repository plugin 0.1.1.

During Run `run_01M0B1DTPGEHFW5YAA6ANR73PA`, the local DVC stage completed its computation but
`mlflow.log_metrics` received HTTP 403 from `/mlflow/api/2.0/mlflow/runs/get` with `Invalid Host header -
possible DNS rebinding attack detected`. The platform then correctly retained the Run as failed.

Expected: the public tracking URL configured by `homebrew-mlflow run` is accepted by MLflow through the
reverse proxy, including the Host/forwarded-host combination. DVC-native metrics remain the workaround for
this repository and are not duplicated through the fluent MLflow API.

Recheck: the public route no longer returns the invalid-Host rejection. However, a managed diagnostic that
called `mlflow.get_run` and `mlflow.log_metric` produced no result after more than three minutes and required
termination of its isolated local process tree. An unauthenticated read reaches the tracking service but
returns HTTP 500 for a missing Run-scoped token rather than a normal authentication status. `doctor` still
reports `mlflow=ok status=200`, so its health check does not establish usable tracking API access.

Verification: after upgrading the repository runtime to `homebrew-mlflow-plugins==0.1.1`, CLI 0.2.4
`doctor` reported both `mlflow_client_auth=ok` and `mlflow_auth_boundary=ok`. Managed diagnostic Run
`run_01M0BHY9H9BN1ZT3YF7RH0452H` then authenticated, read its own Run through `mlflow.get_run`, logged
`platform_auth_diagnostic=1.0`, and finalized as succeeded in about six seconds.

### Service index omits a required repository dependency

**State:** Resolved by the 0.2.3 deployment.

The generated `pyproject.toml` requires `homebrew-mlflow-plugins==0.1.0` from the explicit platform index,
but `https://ml.spkya.ru/packages/simple/homebrew-mlflow-plugins/` returns HTTP 404. Existing frozen
environments continue to work only when the wheel is already available from `uv.lock` and the local cache;
`uv add` cannot resolve the project.

Expected: every package referenced by the current repository template and lock remains available from the
service-hosted simple index, and a clean machine can run `uv sync --frozen` and author dependency changes.

Verification: both the package index page and the exact locked wheel return HTTP 200, and `uv lock --check`
successfully resolves all 166 locked packages.

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
