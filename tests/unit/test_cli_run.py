from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from homebrew_mlflow.cli.main import app
from typer.testing import CliRunner


class Response:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class Client:
    requests: list[tuple[str, dict[str, Any]]] = []

    def __init__(self, **_kwargs: object) -> None:
        pass

    def __enter__(self) -> Client:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def post(self, url: str, **kwargs: Any) -> Response:
        self.requests.append((url, kwargs))
        if url.endswith("/runs"):
            return Response(
                {
                    "id": "run_01K00000000000000000000000",
                    "experiment_id": "exp_01K00000000000000000000000",
                    "logging_token": "run-scoped-token",
                }
            )
        return Response({"state": "succeeded"})


def test_run_executes_child_locally_and_finalizes(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    context = {
        "server": "https://ml.example",
        "project_id": "pr_01K00000000000000000000000",
        "repository_id": "repo_01K00000000000000000000000",
    }
    (tmp_path / ".homebrew-mlflow.json").write_text(json.dumps(context), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("homebrew_mlflow.cli.main.httpx.Client", Client)
    monkeypatch.setattr(
        "homebrew_mlflow.cli.main._refresh_session",
        lambda _client, _server: {"access_token": "access"},
    )
    executed: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if command[:2] == ["git", "rev-parse"]:
            return subprocess.CompletedProcess(command, 0, "a" * 40 + "\n", "")
        executed.append(command)
        assert "hmrf_" not in json.dumps(kwargs.get("env", {}))
        assert kwargs["env"]["MLFLOW_TRACKING_TOKEN"] == "run-scoped-token"
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr("homebrew_mlflow.cli.main.subprocess.run", fake_run)
    Client.requests.clear()

    result = CliRunner().invoke(
        app,
        [
            "run",
            "--experiment",
            "baseline",
            "--pipeline-version",
            "pv_01K00000000000000000000000",
            "--environment",
            "env_01K00000000000000000000000",
            "--",
            "python",
            "train.py",
        ],
    )

    assert result.exit_code == 0, result.output
    assert executed == [["python", "train.py"]]
    assert Client.requests[0][0].endswith("/runs")
    assert Client.requests[0][1]["json"]["pipeline_version_id"].startswith("pv_")
    assert Client.requests[0][1]["json"]["environment_specification_id"].startswith("env_")
    assert Client.requests[-1][0].endswith("/finalize")
    assert Client.requests[-1][1]["json"]["git_commit_sha"] == "a" * 40
