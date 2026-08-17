# {{ repository_name }}

Research repository for **{{ project_name }}**, managed by Homebrew MLflow.

- Platform project: `{{ project_id }}`
- Repository: `{{ repository_id }}`
- Platform: {{ platform_url }}

## Work locally

Install the service-hosted helper, then authenticate and verify the repository configuration:

```text
uv tool install --default-index {{ platform_url }}/packages/simple/ homebrew-mlflow
homebrew-mlflow login --server {{ platform_url }}
homebrew-mlflow doctor
```

Git and DVC remain native tools. A normal experiment looks like:

```text
dvc status
homebrew-mlflow run --experiment <name> -- dvc exp run -n <experiment-name>
dvc metrics show
```

`dvc push -r platform` transfers DVC objects but does not publish them. To archive an immutable result,
commit and push its Git/DVC metadata, then use `scripts/dvc-publish.sh` or
`scripts/dvc-publish.ps1` with the selector and IDs supplied by the platform UI.

AI-assisted work follows `AGENTS.md`. Review its branch, compute-cost, credential, and publication rules
before asking an agent to run an experiment.
