# Tested human workflows

This file contains only command sequences that have been exercised successfully while testing the platform.
A successful sequence means that the commands themselves completed without breaking local state; it does
not imply that later, unlisted workflow stages are working.

Install or replace the global platform CLI with the tested service-hosted release.

```powershell
uv tool install --force --default-index https://ml.spkya.ru/packages/simple/ --no-build "homebrew-mlflow==0.2.9"
homebrew-mlflow version
```

Authenticate once on a machine, then reconcile this clone's generated repository and credential configuration.

```powershell
homebrew-mlflow login --server https://ml.spkya.ru
homebrew-mlflow repository configure
git diff
uv sync --frozen
uv run --frozen python -c "import importlib.metadata as m; print(m.version('homebrew-mlflow-plugins'))"
git status --short
```

Review and commit any generated migration shown above before running experiments; do not commit unrelated changes.
The tested template-v8 migration reports plugin version `0.1.6` and pins the same version in both
`pyproject.toml` and `uv.lock`.

Materialize the repository-pinned Python environment and DVC data without changing locked dependency resolution.

```powershell
uv sync --frozen
uv run --frozen dvc pull -r platform
uv run --frozen dvc status
```

Confirm that the branch has no local changes, has no unpushed commits, and passes local and remote readiness checks.

```powershell
git status --short
git rev-list --count '@{u}..HEAD'
homebrew-mlflow doctor
```

The expected Git outputs above are no status entries and a commit count of `0`.

Start one isolated experiment branch from a clean reviewed parent before changing experiment code.

```powershell
git switch -c experiment/YYYYMMDD-short-description
git status --short --branch
```

Run the repository's tested local validation set before committing an experiment implementation or result.

```powershell
uv run --frozen python -m unittest discover -s tests
uv run --frozen dvc status
git diff --check
```

Commit a coherent experiment state, push its non-protected branch, and verify that no commit remains local.

```powershell
git add <paths>
git commit -m "<message>"
git push -u origin <branch>
git rev-list --count '@{u}..HEAD'
```

The expected final count is `0`; do not describe a branch as shared until the push succeeds.

Record a new named DVC experiment through the repository's declared environment.

```powershell
homebrew-mlflow run --experiment <name> -- dvc exp run -n <name>
```

Before a new managed Run, expose only its compact metric file to DVC tracking. Move the preceding active
stage's metric path from `metrics:` into that stage's `outs:` list, and add the new path under the new
stage's `metrics:` list. Do not delete or rewrite completed metric files. The declaration guard must pass;
`dvc metrics show` should display only the active experiment's materialized metrics, or nothing before its
first execution.

```powershell
uv run --frozen python -m unittest tests.test_dvc_tracking_contract -v
uv run --frozen dvc metrics show
```

Record a Run that consumes an already-published immutable dataset version.

```powershell
homebrew-mlflow run --experiment <name> --input-version <av-version-id> -- dvc exp run -n <name> <stage>
```

The `--input-version` value must be an exact published `av_...` ID, not an Artifact family name or alias.

Record a Run that consumes multiple immutable Artifact Versions, such as a dataset and a previously
published model. Repeat `--input-version`; preserve the order in the handoff for clear lineage.

```powershell
homebrew-mlflow run --experiment <name> `
  --input-version <dataset-av-version-id> `
  --input-version <model-av-version-id> `
  -- dvc exp run -n <dvc-experiment-name> <training-stage>
```

Materialize and share an expensive reusable preprocessing stage before a managed training Run. This
sequence was exercised by the purchase-episode/reactivation experiment: the first repro wrote the cache
and lock entry, the targeted push transferred its ten DVC objects, and the subsequent managed Run reported
that the preprocessing stage did not change and skipped it.

```powershell
uv run --frozen dvc repro <preprocessing-stage>
uv run --frozen dvc status <preprocessing-stage>
uv run --frozen dvc push -r platform <preprocessing-output>
git add dvc.lock
git commit -m "Record cached <feature-name> features"
git push
git rev-list --count '@{u}..HEAD'
uv run --frozen dvc repro --dry <training-stage>

homebrew-mlflow run --experiment <name> `
  --input-version <dataset-av-version-id> `
  --input-version <model-av-version-id> `
  -- dvc exp run -n <dvc-experiment-name> <training-stage>
```

The dry run must say the preprocessing stage did not change. Do not commit or push raw cache files through
Git; only its `dvc.lock` identity belongs in Git. A DVC push makes the cache recoverable but does not publish
it as a platform Artifact Version.

Validate a final competition submission after a production refit and before publishing its model bundle.
This sequence was exercised on the 250,000-row purchase-episode/reactivation refit output.

```powershell
uv run --frozen python -c "import pandas as pd,numpy as np; s=pd.read_csv('sample_submit.csv'); p=pd.read_csv('<submission.csv>'); assert list(p.columns)==['user_id','predict']; assert len(p)==250000 and p.user_id.nunique()==250000; assert np.array_equal(p.user_id.to_numpy(),s.user_id.to_numpy()); assert np.isfinite(p.predict).all() and (p.predict>=0).all(); print('submission=ok rows=250000')"
uv run --frozen dvc status <production-stage>
uv run --frozen dvc push -r platform
```

Structural validation confirms only the submission contract, not leaderboard quality. Keep the successful
evaluation metric and its immutable model input attached to the production Run lineage.

Run, inspect, transfer, publish, and verify a trained model as the canonical tested lineage-preserving
sequence. This full lifecycle was exercised by the temporal-anchor-bagging evaluation Run. An
operationally successful Run can still reject its scientific hypothesis; publish its trained evaluation
bundle for honest lineage, but do not run a gated production refit when the declared metric gate fails.

Before the Run, ensure the DVC stage declares its data, source, parameter, metric, report, and complete
model-bundle dependencies/outputs. The training entry point must set a human-readable
`mlflow.note.content` before its first fit and must not upload model binaries through MLflow. Generate the
schema-only signature, then commit and push all reproducibility inputs before training.

```powershell
uv sync --frozen
homebrew-mlflow doctor
uv run --frozen dvc metrics show
uv run --frozen dvc status <training-stage>
git diff --check
git status --short --branch
git add dvc.yaml params.yaml <source-and-test-paths> <committed-signature.json>
git commit -m "Add <experiment-name> experiment"
git push -u origin <branch>
git rev-list --count '@{u}..HEAD'

homebrew-mlflow run --experiment <name> --input-version <dataset-av-version-id> -- dvc exp run -n <dvc-experiment-name> <training-stage>

uv run --frozen dvc exp show --no-pager
uv run --frozen dvc metrics show
uv run --frozen dvc status <training-stage>
uv run --frozen dvc exp list --all
git diff -- dvc.lock

git add dvc.lock <experiment-ledger-and-report-paths>
git commit -m "Record <experiment-name> result"
git push
git status --short
git rev-list --count '@{u}..HEAD'

uv run --frozen dvc exp push origin <dvc-experiment-name> -r platform -j 2
uv run --frozen dvc status -c -r platform

scripts/dvc-publish.ps1 --pipeline dvc.yaml --stage <training-stage> --out <model-output> --artifact <model-artifact> --run-id <producing-run-id> --signature <committed-signature.json>

homebrew-mlflow doctor
git status --short --branch
git rev-list --left-right --count '@{u}...HEAD'
```

Capture the managed Run ID from the wrapper output and use that exact value as `<producing-run-id>`; do
not reuse an earlier Run. The expected pre-Run unpushed count is `0`, publication must reach
`operation.published`, final readiness must be `ok`, and the final left/right Git counts must be `0 0`.
Verify in the platform that the Artifact Version is `available` and `verified`, its `producing_run_id` and
the Run's immutable output-version list match, the exact dataset `av_...` appears as an input, provenance
is complete, and the Run and model family descriptions are populated. Treat a missing description,
incomplete bundle, failed publication, or mismatched Run pin as a blocker.

Inspect the resulting experiment history and current DVC-native metrics.

```powershell
uv run --frozen dvc exp show --no-pager
uv run --frozen dvc metrics show
```

Compare local named experiment refs with refs already shared on the Git remote.

```powershell
uv run --frozen dvc exp list --all
uv run --frozen dvc exp list origin --all
```

Materialize the outputs recorded by the current `dvc.lock` from the local DVC cache and verify consistency.

```powershell
uv run --frozen dvc checkout
uv run --frozen dvc status
```

Transfer a named DVC experiment ref and its cached outputs to the Git and platform DVC remotes.

```powershell
uv run --frozen dvc exp push origin <experiment-name> -r platform -j 2
```

Transfer already-produced DVC objects and verify remote synchronization without publishing an artifact.

```powershell
uv run --frozen dvc push -r platform
uv run --frozen dvc status -c -r platform
```

Create reusable artifact families once, list their immutable IDs, and publish one committed DVC output
through the supported PowerShell workflow after its Git branch and DVC objects are shared.

```powershell
homebrew-mlflow artifact create <artifact-family-name>
homebrew-mlflow artifact list
uv run --frozen dvc exp push origin <experiment-name> -r platform -j 2
git status --short
git rev-list --count '@{u}..HEAD'
scripts/dvc-publish.ps1 --pipeline dvc.yaml --stage <stage> --out <output> --artifact <artifact-family-name> --run-id <run-id>
```

The publication command is successful only after its event stream reaches `operation.published`. Use an
isolated clean Git worktree at the exact DVC experiment revision when archiving historical outputs; never
attach an incomplete or unrelated Run merely to populate `--run-id`.

Retry only failed publication registrations after the platform confirms that the publication worker is
repaired. First verify that the original stages remain materialized and that their Git state is already
shared; do not retrain or transfer unchanged DVC objects again.

```powershell
uv run --frozen dvc status <first-stage> <second-stage>
git status --short
git rev-list --count '@{u}..HEAD'
```

The tested retry used the same semantic selectors, Artifact families, and successful producing Run as the
failed attempts. Each invocation creates a new publication operation and must independently reach
`operation.published` before its new immutable `av_...` version is considered available.

```powershell
scripts/dvc-publish.ps1 --pipeline dvc.yaml --stage <first-stage> --out <first-output> --artifact <first-artifact-family> --run-id <original-run-id>
scripts/dvc-publish.ps1 --pipeline dvc.yaml --stage <second-stage> --out <second-output> --artifact <second-artifact-family> --run-id <original-run-id>
```

Do not use this sequence if the failed operation created an Artifact Version, if the selected Git/DVC state
has changed, or if the Run did not produce the outputs. In those cases, stop and inspect lineage instead of
creating duplicate or incorrectly attributed versions.

Generate a schema-only model signature, commit it before publication, and publish a model output with that
signature and its producing Run.

```powershell
uv run --frozen python scripts/generate-model-signature.py --manifest <model-manifest> --output model-signature.json
git add model-signature.json
git commit -m "Add model signature"
git push
scripts/dvc-publish.ps1 --pipeline dvc.yaml --stage <stage> --out <model-output> --artifact <model-artifact> --run-id <run-id> --signature model-signature.json
```

Register and publish one standalone DVC-tracked dataset without rerunning a pipeline stage.

```powershell
homebrew-mlflow artifact create <dataset-name> --kind dataset --description "<description>"
uv run --frozen dvc push -r platform <dataset>.dvc
scripts/dvc-publish.ps1 --dvc-file <dataset>.dvc --out <dataset> --artifact <dataset-name> --run-id <producer-run-id>
```

This repository successfully exercised the standalone flow for `train.parquet.dvc`; a future consuming
Run must pass its exact published `av_...` version through `--input-version` rather than an Artifact name.

Force an unchanged pipeline to recompute only when deliberately testing reproducibility or platform capture.

```powershell
homebrew-mlflow run --experiment <name> -- dvc exp run --force -n <name>
```

CLI 0.2.9 may print `homebrew-mlflow run-recover <RUN_ID>` when finalization cannot recover connectivity.
Run that exact command instead of rerunning computation. It is intentionally not presented above as a
tested sequence until this repository exercises a real journal-based recovery successfully.
