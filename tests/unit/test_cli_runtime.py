from __future__ import annotations

from pathlib import Path

import pytest
from homebrew_mlflow.cli.main import _runtime_dvc_version_command
from homebrew_mlflow.cli.repository import (
    DvcConfiguration,
    install_dvc_profile,
    prepare_repository_template_upgrade,
    read_repository_dvc_configuration,
    reconcile_repository_configuration,
)
from homebrew_mlflow.cli.runtime import resolve_runtime


def test_tracked_runtime_config_supplies_defaults_and_overrides(tmp_path: Path) -> None:
    (tmp_path / "homebrew-mlflow.toml").write_text(
        '[environment]\nkind = "uv"\nname = "training"\n\n[secrets]\nenabled = true\n',
        encoding="utf-8",
    )

    configured = resolve_runtime(tmp_path, ["python", "train.py"])
    overridden = resolve_runtime(
        tmp_path,
        ["python", "train.py"],
        kind_override="system",
        name_override="debug",
        secrets_override=False,
    )

    assert (configured.kind, configured.name, configured.secrets_enabled) == (
        "uv",
        "training",
        True,
    )
    assert (overridden.kind, overridden.name, overridden.secrets_enabled) == (
        "system",
        "debug",
        False,
    )


def test_runtime_detection_fails_when_repository_is_ambiguous(tmp_path: Path) -> None:
    (tmp_path / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("numpy==2.0\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="exactly one"):
        resolve_runtime(tmp_path, ["python", "train.py"])


def test_doctor_uses_repository_pinned_dvc_for_uv(tmp_path: Path) -> None:
    assert _runtime_dvc_version_command(tmp_path, "uv", "default") == [
        "uv",
        "run",
        "--frozen",
        "--",
        "dvc",
        "--version",
    ]


def test_repository_configure_merges_unique_profile_without_losing_user_config(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repository"
    (root / ".aws").mkdir(parents=True)
    (root / ".aws" / "config").write_text(
        "[profile homebrew-mlflow-project_123]\n"
        "region = us-east-1\n"
        "credential_process = homebrew-mlflow credentials dvc --project project_123\n",
        encoding="utf-8",
    )
    target = tmp_path / "user" / "config"
    target.parent.mkdir()
    target.write_text("[default]\nregion = eu-west-1\n", encoding="utf-8")

    profile, written = install_dvc_profile(root, aws_config=target)

    content = target.read_text(encoding="utf-8")
    assert profile == "homebrew-mlflow-project_123"
    assert written == target
    assert "[default]" in content
    assert "[profile homebrew-mlflow-project_123]" in content
    assert "credential_process = homebrew-mlflow credentials dvc --project project_123" in content


def test_repository_configuration_repairs_v2_and_is_idempotent(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    (root / ".dvc").mkdir(parents=True)
    (root / ".aws").mkdir()
    (root / ".dvc" / "config").write_text(
        "[core]\n    remote = platform\n['remote \"platform\"']\n"
        "    url = s3://research/dvc\n"
        "    endpointurl = https://old.example\n"
        "    profile = homebrew-mlflow-project_123\n"
        "['remote \"personal\"']\n    url = s3://personal/cache\n",
        encoding="utf-8",
    )
    (root / ".aws" / "config").write_text(
        "[profile personal]\nregion = eu-west-1\n", encoding="utf-8"
    )
    target = tmp_path / "user-aws-config"
    configuration = DvcConfiguration(
        "platform",
        "s3://research/dvc/project_123",
        "https://objects.example",
        "homebrew-mlflow-project_123",
        "homebrew-mlflow credentials dvc --project project_123",
    )

    profile, written, changed = reconcile_repository_configuration(
        root, configuration, aws_config=target
    )
    second = reconcile_repository_configuration(root, configuration, aws_config=target)

    assert profile == "homebrew-mlflow-project_123"
    assert written == target
    assert {path.relative_to(root).as_posix() for path in changed} == {
        ".dvc/config",
        ".dvc/.gitignore",
        ".aws/config",
    }
    assert second[2] == ()
    assert read_repository_dvc_configuration(root) == configuration
    assert "personal" in (root / ".dvc" / "config").read_text(encoding="utf-8")
    assert "profile personal" in (root / ".aws" / "config").read_text(encoding="utf-8")
    assert (root / ".dvc" / ".gitignore").read_text(encoding="utf-8") == "/tmp\n/cache\n"


def test_repository_template_upgrade_preserves_custom_text_and_is_idempotent(
    tmp_path: Path,
) -> None:
    (tmp_path / ".homebrew-mlflow.json").write_text(
        '{"server":"https://ml.example","project_id":"pr_1",'
        '"repository_id":"repo_1","template_version":2}\n',
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text(
        "custom introduction\n"
        "dvc status\n"
        "homebrew-mlflow run --experiment <name> -- dvc exp run -n <experiment-name>\n"
        "dvc metrics show\n```\n"
        "`dvc push -r platform` transfers objects\n",
        encoding="utf-8",
    )
    (tmp_path / "AGENTS.md").write_text(
        "recent commits, `dvc status`, and `dvc dag` before changing\n"
        "homebrew-mlflow run --experiment <name> -- dvc exp run -n <name>\n```\n"
        "dvc status\ndvc push -r platform\ncustom rule\n",
        encoding="utf-8",
    )

    prepared = prepare_repository_template_upgrade(tmp_path)
    changed = prepared.apply()
    second = prepare_repository_template_upgrade(tmp_path).apply()

    assert {path.name for path in changed} == {
        ".homebrew-mlflow.json",
        "README.md",
        "AGENTS.md",
    }
    assert second == ()
    assert '"template_version": 4' in (tmp_path / ".homebrew-mlflow.json").read_text()
    assert "custom introduction" in (tmp_path / "README.md").read_text()
    assert "uv run --frozen dvc status" in (tmp_path / "README.md").read_text()
    assert "custom rule" in (tmp_path / "AGENTS.md").read_text()
    assert "uv run --frozen dvc dag" in (tmp_path / "AGENTS.md").read_text()


def test_repository_template_upgrade_conflict_does_not_write(tmp_path: Path) -> None:
    sentinel = '{"template_version":3}\n'
    (tmp_path / ".homebrew-mlflow.json").write_text(sentinel, encoding="utf-8")
    (tmp_path / "README.md").write_text("researcher README\n", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("researcher instructions\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="conflicts with researcher changes"):
        prepare_repository_template_upgrade(tmp_path)

    assert (tmp_path / ".homebrew-mlflow.json").read_text(encoding="utf-8") == sentinel
