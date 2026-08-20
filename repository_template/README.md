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
homebrew-mlflow repository configure
homebrew-mlflow doctor
uv sync --frozen
```

Git and DVC remain native tools. A normal experiment looks like:

```text
uv run --frozen dvc status
homebrew-mlflow run --experiment <name> -- dvc exp run -n <experiment-name>
uv run --frozen dvc metrics show
```

The Run helper executes its child through the selected uv environment, so the command after `--` stays
`dvc ...` rather than nesting another `uv run`.

After the exact named DVC experiment revision is resolved, scalar DVC metrics and parameters are copied
to the managed Run automatically. Values explicitly logged through MLflow take precedence. DVC datasets,
models, checkpoints, and reports remain immutable Artifact Versions rather than MLflow binary bundles.

The tracked `homebrew-mlflow.toml` selects the default `uv` environment. The CLI captures and resolves
the exact lock/runtime revision automatically; `.venv` remains local while `uv.lock` is committed.

`uv run --frozen dvc push -r platform` transfers DVC objects but does not publish them. Create an artifact family once
with `homebrew-mlflow artifact create <name> --kind dataset|model|checkpoint|report|generic`. Classify the
family correctly before publication. To archive an immutable result,
commit and push its Git/DVC metadata, then use `scripts/dvc-publish.sh` or
`scripts/dvc-publish.ps1` with the selector and artifact name or ID.

Model publications also require a committed interface sidecar. Generate it from an inferred or explicit
MLflow `ModelSignature` with
`homebrew_mlflow.mlflow_plugins.signature.write_model_signature("model-signature.json", signature)`,
commit it, and pass `--signature model-signature.json` to the publication script. The sidecar records only
typed inputs and outputs; model bytes and datasets remain canonical DVC Artifact Versions.

AI-assisted work follows `AGENTS.md`. Review its branch, compute-cost, credential, and publication rules
before asking an agent to run an experiment.
