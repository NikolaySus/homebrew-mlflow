from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import httpx
import pytest
from homebrew_mlflow.cli.main import (
    _changed_dvc_experiment,
    _dvc_lock_output_paths,
    _recover_finalization,
    _send_run_heartbeats,
    _write_private_json,
    app,
)
from homebrew_mlflow.cli.runtime import RuntimeCapture, RuntimeSelection
from typer.testing import CliRunner


class Response:
    def __init__(self, payload: dict[str, Any], status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

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

    def put(self, url: str, **_kwargs: Any) -> Response:
        if url.endswith("/environment-specifications/resolve"):
            return Response({"id": "env_01K00000000000000000000000"})
        if url.endswith("/pipeline-versions/resolve"):
            return Response({"id": "pv_01K00000000000000000000000"})
        raise AssertionError(url)


@pytest.mark.parametrize(
    ("experiment_refs_after", "expected_provenance", "expected_revision"),
    [
        ({"refs/exps/base/baseline": "b" * 40}, "complete", "b" * 40),
        ({}, "incomplete", None),
    ],
)
def test_run_preserves_child_success_with_workspace_changes(  # type: ignore[no-untyped-def]
    monkeypatch,
    tmp_path: Path,
    experiment_refs_after: dict[str, str],
    expected_provenance: str,
    expected_revision: str | None,
) -> None:
    context = {
        "server": "https://ml.example",
        "project_id": "pr_01K00000000000000000000000",
        "repository_id": "repo_01K00000000000000000000000",
    }
    (tmp_path / ".homebrew-mlflow.json").write_text(json.dumps(context), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "homebrew_mlflow.cli.main.typer.get_app_dir", lambda _name: str(tmp_path / "app-data")
    )
    monkeypatch.setattr("homebrew_mlflow.cli.main.httpx.Client", Client)
    monkeypatch.setattr(
        "homebrew_mlflow.cli.main._refresh_session",
        lambda _client, _server: {"access_token": "access"},
    )
    selection = RuntimeSelection("system", "default", False)
    capture = RuntimeCapture(selection, {"kind": "system"}, "f" * 64, ("python", "train.py"))
    monkeypatch.setattr(
        "homebrew_mlflow.cli.main.resolve_runtime", lambda *_args, **_kwargs: selection
    )
    monkeypatch.setattr(
        "homebrew_mlflow.cli.main.capture_runtime", lambda *_args, **_kwargs: capture
    )
    monkeypatch.setattr(
        "homebrew_mlflow.cli.main._committed_upstream_state", lambda _root: "a" * 40
    )
    ref_snapshots = iter(({}, experiment_refs_after))
    monkeypatch.setattr(
        "homebrew_mlflow.cli.main._dvc_experiment_refs", lambda _root: next(ref_snapshots)
    )
    monkeypatch.setattr(
        "homebrew_mlflow.cli.main._git_output",
        lambda *arguments, **_kwargs: (
            "a" * 40
            if arguments[:2] in {("rev-parse", "HEAD"), ("merge-base", "a" * 40)}
            else " M dvc.lock"
            if arguments[:2] == ("status", "--porcelain")
            else ""
        ),
    )
    monkeypatch.setattr("homebrew_mlflow.cli.main._git_blob_evidence", lambda *_args: None)
    monkeypatch.setattr(
        "homebrew_mlflow.cli.main._capture_dvc_tracking",
        lambda *_args, **_kwargs: {
            "status": "captured",
            "metrics_imported": 0,
            "parameters_imported": 0,
            "warnings": [],
        },
    )
    executed: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        executed.append(command)
        assert "hmrf_" not in json.dumps(kwargs.get("env", {}))
        token_file = Path(kwargs["env"]["MLFLOW_TRACKING_TOKEN_FILE"])
        assert token_file.read_text(encoding="utf-8") == "run-scoped-token"
        assert kwargs["env"]["MLFLOW_TRACKING_AUTH"] == "homebrew-token-file"
        assert "MLFLOW_TRACKING_TOKEN" not in kwargs["env"]
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr("homebrew_mlflow.cli.main.subprocess.run", fake_run)
    Client.requests.clear()

    result = CliRunner().invoke(
        app,
        [
            "run",
            "--experiment",
            "baseline",
            "--",
            "python",
            "train.py",
        ],
    )

    assert result.exit_code == 0, result.output
    assert executed == [["python", "train.py"]]
    create = next(request for request in Client.requests if request[0].endswith("/runs"))
    assert create[1]["json"]["pipeline_version_id"] is None
    assert create[1]["json"]["environment_specification_id"].startswith("env_")
    assert Client.requests[-1][0].endswith("/finalize")
    assert Client.requests[-1][1]["json"]["git_commit_sha"] == "a" * 40
    assert Client.requests[-1][1]["json"]["provenance_status"] == expected_provenance
    assert Client.requests[-1][1]["json"]["dvc_experiment_revision"] == expected_revision


def test_changed_dvc_experiment_resolves_one_descendant(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    base = "a" * 40
    revision = "b" * 40
    monkeypatch.setattr(
        "homebrew_mlflow.cli.main._git_output",
        lambda *arguments, **_kwargs: base if arguments[0] == "merge-base" else "",
    )

    resolved, refs, problems = _changed_dvc_experiment(
        tmp_path,
        base,
        {"refs/exps/base/existing": "c" * 40},
        {
            "refs/exps/base/existing": "c" * 40,
            "refs/exps/base/new": revision,
        },
    )

    assert resolved == revision
    assert refs == ["refs/exps/base/new"]
    assert problems == []


def test_dvc_lock_output_paths_are_captured_without_trusting_hashes() -> None:
    content = b"""schema: '2.0'
stages:
  train:
    cmd: python train.py
    outs:
    - path: models/model.bin
      hash: md5
      md5: secret-not-authoritative
    metrics:
    - path: metrics.json
  evaluate:
    outs:
    - path: reports/results.csv
"""

    assert _dvc_lock_output_paths(content) == [
        "models/model.bin",
        "reports/results.csv",
    ]


def test_heartbeat_continues_after_transient_connection_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    attempts = 0

    class HeartbeatClient(Client):
        def post(self, url: str, **kwargs: Any) -> Response:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise httpx.ConnectError("offline", request=httpx.Request("POST", url))
            return Response({"logging_token": "refreshed-token"})

    class StopAfterSuccess:
        calls = 0

        def wait(self, _timeout: float) -> bool:
            self.calls += 1
            return self.calls > 2

    token_path = tmp_path / "token"
    token_path.write_text("initial-token", encoding="utf-8")
    monkeypatch.setattr("homebrew_mlflow.cli.main.httpx.Client", HeartbeatClient)
    monkeypatch.setattr(
        "homebrew_mlflow.cli.main._refresh_session",
        lambda _client, _server: {"access_token": "access"},
    )
    errors: list[str] = []

    _send_run_heartbeats(StopAfterSuccess(), "https://ml.example", "run_1", token_path, errors)  # type: ignore[arg-type]

    assert attempts == 2
    assert errors == ["ConnectError"]
    assert token_path.read_text(encoding="utf-8") == "refreshed-token"


def test_finalization_journal_replays_same_key_after_connection_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    attempts: list[dict[str, Any]] = []

    class RecoverClient(Client):
        def post(self, url: str, **kwargs: Any) -> Response:
            attempts.append(kwargs)
            if len(attempts) == 1:
                raise httpx.ConnectError("offline", request=httpx.Request("POST", url))
            return Response({"state": "succeeded"})

    path = tmp_path / "pending.json"
    _write_private_json(
        path,
        {
            "schema": 1,
            "server": "https://ml.example",
            "run_id": "run_1",
            "project_id": "pr_1",
            "idempotency_key": "stable-key",
            "pipeline_resolution": None,
            "finalization": {"exit_code": 0, "status": "succeeded"},
        },
    )
    monkeypatch.setattr("homebrew_mlflow.cli.main.httpx.Client", RecoverClient)
    monkeypatch.setattr("homebrew_mlflow.cli.main.time.sleep", lambda _delay: None)
    monkeypatch.setattr(
        "homebrew_mlflow.cli.main._refresh_session",
        lambda _client, _server: {"access_token": "access", "project_id": "pr_1"},
    )

    result = _recover_finalization(path)

    assert result["state"] == "succeeded"
    assert [request["headers"]["Idempotency-Key"] for request in attempts] == [
        "stable-key",
        "stable-key",
    ]
    assert not path.exists()
