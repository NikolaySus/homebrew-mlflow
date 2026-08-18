# Platform test notes

These notes track reproducible platform or generated-workflow problems found while using this repository.
User command mistakes and problems caused solely by an outdated local CLI are intentionally excluded.

## 2026-08-18

### Generated DVC credentials cannot access the configured remote

**State:** Confirmed, blocking DVC transfers.

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

### `doctor` reports DVC OK without validating remote access

**State:** Confirmed, misleading readiness result.

`homebrew-mlflow doctor` reports `git=ok` and `dvc=ok` even though the generated DVC configuration receives
HTTP 403 from object storage immediately afterward.

Expected: repository readiness checking should safely validate credential-process execution and a
non-mutating operation against the configured project prefix, or clearly report that `dvc=ok` checks only
the local executable/configuration and does not verify remote access.

### First DVC use makes a clean generated repository dirty

**State:** Confirmed, blocks the Run workflow.

The initial repository contains `.dvc/config` but omits the normal `.dvc/.gitignore`. When DVC first creates
its local cache state, it creates `.dvc/.gitignore`; that file is not ignored by the root `.gitignore`. The
next Run therefore fails before executing its command:

```text
RuntimeError: repository has uncommitted changes
```

Expected: normal setup and DVC inspection should leave a freshly cloned repository clean. Include the
standard `.dvc/.gitignore` in the repository template or ignore the generated local file at the root.

### Generated uv instructions can execute a global DVC on Windows

**State:** Confirmed, causes environment/version drift.

The generated README runs `uv sync --frozen` and then shows plain `dvc` commands. On Windows, `uv sync`
does not activate `.venv`; plain `dvc` can therefore select a global installation. In this test it selected
global DVC 3.59.1 instead of the repository-pinned DVC 3.67.1 and failed inside the S3 credential subprocess
path with `NotImplementedError`.

Expected: generated commands should be environment-independent. Prefer `uv run --frozen dvc ...` (including
inside the `homebrew-mlflow run` examples and publication scripts), or explicitly require and verify virtual
environment activation.
