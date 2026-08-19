from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from homebrew_mlflow.cli.main import app
from typer.testing import CliRunner


class Response:
    def __init__(self, payload: Any = None, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.is_success = 200 <= status_code < 300

    def json(self) -> Any:
        return self._payload

    def raise_for_status(self) -> None:
        if not self.is_success:
            raise RuntimeError(f"HTTP {self.status_code}")


class Client:
    def __init__(self, **_kwargs: object) -> None:
        pass

    def __enter__(self) -> Client:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def get(self, url: str, **_kwargs: object) -> Response:
        if url.endswith("/client-releases/recommended"):
            return Response(
                {
                    "release": {
                        "recommended_version": "0.2.1",
                        "compatible_versions": ">=0.2,<0.3",
                    }
                }
            )
        if url.endswith("/repositories"):
            return Response([{"id": "repo_test", "state": "active"}])
        if url.endswith("/dvc-configuration"):
            return Response(
                {
                    "remote_name": "platform",
                    "remote_url": "s3://research/dvc/pr_test",
                    "endpoint_url": "https://objects.example",
                    "profile": "homebrew-mlflow-pr_test",
                    "credential_process": (
                        "homebrew-mlflow credentials dvc --project pr_test"
                    ),
                }
            )
        if url.endswith("/api/v1/diagnostics/mlflow"):
            return Response({"status": "ready", "backend_status": 401})
        raise AssertionError(url)


def test_doctor_uses_pinned_dvc_and_probes_project_remote(
    tmp_path: Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    (tmp_path / ".dvc").mkdir()
    (tmp_path / ".aws").mkdir()
    (tmp_path / ".homebrew-mlflow.json").write_text(
        json.dumps(
            {
                "server": "https://ml.example",
                "project_id": "pr_test",
                "repository_id": "repo_test",
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "homebrew-mlflow.toml").write_text(
        '[environment]\nkind = "uv"\nname = "default"\n\n[secrets]\nenabled = false\n',
        encoding="utf-8",
    )
    (tmp_path / ".dvc" / "config").write_text(
        "[core]\nremote = platform\n['remote \"platform\"']\n"
        "url = s3://research/dvc/pr_test\n"
        "endpointurl = https://objects.example\n"
        "profile = homebrew-mlflow-pr_test\n",
        encoding="utf-8",
    )
    (tmp_path / ".aws" / "config").write_text(
        "[profile homebrew-mlflow-pr_test]\nregion = us-east-1\n"
        "credential_process = homebrew-mlflow credentials dvc --project pr_test\n",
        encoding="utf-8",
    )
    commands: list[list[str]] = []

    def run(command: list[str], **_kwargs: object) -> SimpleNamespace:
        commands.append(command)
        if "homebrew_mlflow.mlflow_plugins.diagnostics" in command:
            output = "mlflow_client_auth=ok\nmlflow_auth_boundary=ok\n"
        else:
            output = "3.67.1\n" if "dvc" in command else "git version 2.50.0\n"
        return SimpleNamespace(returncode=0, stdout=output, stderr="")

    probes: list[str] = []
    monkeypatch.setattr("homebrew_mlflow.cli.main.httpx.Client", Client)
    monkeypatch.setattr(
        "homebrew_mlflow.cli.main._refresh_session",
        lambda _client, _server: {"access_token": "platform-token"},
    )
    monkeypatch.setattr("homebrew_mlflow.cli.main._configured_server", lambda: "https://ml.example")
    monkeypatch.setattr("homebrew_mlflow.cli.main.repository_root", lambda *_args: tmp_path)
    monkeypatch.setattr("homebrew_mlflow.cli.main.subprocess.run", run)
    monkeypatch.setattr(
        "homebrew_mlflow.cli.main._probe_dvc_remote",
        lambda configuration: probes.append(configuration.remote_url),
    )
    monkeypatch.setattr("homebrew_mlflow.cli.main.shutil.which", lambda _name: "uv")

    result = CliRunner().invoke(app, ["doctor"])

    assert result.exit_code == 0, result.output
    assert "readiness=ok" in result.output
    assert ["uv", "run", "--frozen", "--", "dvc", "--version"] in commands
    assert "mlflow_service=ok status=200" in result.output
    assert "mlflow_client_auth=ok" in result.output
    assert "mlflow_auth_boundary=ok" in result.output
    assert probes == ["s3://research/dvc/pr_test"]
