from __future__ import annotations

from pathlib import Path

import pytest
from homebrew_mlflow.cli.repository import install_dvc_profile
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
