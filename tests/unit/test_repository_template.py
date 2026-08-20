from pathlib import Path

import pytest
from homebrew_mlflow.application import RepositoryTemplateContext
from homebrew_mlflow.domain import PublicId, ResourceKind
from homebrew_mlflow.infrastructure import FileSystemRepositoryTemplate, RepositoryTemplateError


def context() -> RepositoryTemplateContext:
    return RepositoryTemplateContext(
        repository_id=PublicId.generate(ResourceKind.REPOSITORY),
        project_id=PublicId.generate(ResourceKind.PROJECT),
        project_name="Protein Folding",
        repository_name="Baseline Models",
        repository_slug="baseline-models",
        platform_url="https://ml.example",
        dvc_remote_url="s3://research/project/dvc",
        s3_endpoint_url="https://objects.example",
    )


def test_checked_in_template_renders_all_required_files() -> None:
    root = Path(__file__).parents[2] / "repository_template"
    template_context = context()
    rendered = FileSystemRepositoryTemplate(root).render(template_context)
    by_path = {file.path: file for file in rendered}

    assert {
        ".aws/config",
        ".dvc/.gitignore",
        ".dvc/config",
        ".homebrew-mlflow.json",
        ".gitignore",
        "AGENTS.md",
        "README.md",
        "dvc.yaml",
        "homebrew-mlflow.toml",
        "pyproject.toml",
        "uv.lock",
        "examples/mlflow_autolog.py",
        "scripts/dvc-publish.ps1",
        "scripts/dvc-publish.sh",
        "scripts/run-experiment.py",
        "train.py",
    } <= by_path.keys()
    assert "{{" not in "".join(file.content for file in rendered)
    assert str(template_context.project_id) in by_path["README.md"].content
    assert str(template_context.repository_id) in by_path["README.md"].content
    assert '"template_version": 8' in by_path[".homebrew-mlflow.json"].content
    assert "homebrew-mlflow-plugins==0.1.6" in by_path["pyproject.toml"].content
    assert "--signature model-signature.json" in by_path["AGENTS.md"].content
    assert "uv run --frozen dvc status" in by_path["README.md"].content
    assert by_path["scripts/dvc-publish.sh"].executable
    assert not by_path["scripts/dvc-publish.ps1"].executable
    assert not by_path["examples/mlflow_autolog.py"].executable


def test_renderer_rejects_unknown_placeholder(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("{{ unknown_value }}", encoding="utf-8")

    with pytest.raises(RepositoryTemplateError, match="unknown_value"):
        FileSystemRepositoryTemplate(tmp_path).render(context())
