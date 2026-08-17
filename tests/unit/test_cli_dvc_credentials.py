from __future__ import annotations

import json
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
    def __init__(self, **_kwargs: object) -> None:
        pass

    def __enter__(self) -> Client:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def post(self, url: str, **_kwargs: Any) -> Response:
        if url.endswith("/auth/exchange"):
            return Response({"access_token": "dvc-audience-token"})
        return Response(
            {
                "Version": 1,
                "AccessKeyId": "temporary-access",
                "SecretAccessKey": "temporary-secret",
                "SessionToken": "temporary-session",
                "Expiration": "2026-08-17T13:00:00Z",
            }
        )


def test_dvc_credentials_emits_only_aws_process_json(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr("homebrew_mlflow.cli.main.httpx.Client", Client)
    monkeypatch.setattr(
        "homebrew_mlflow.cli.main._refresh_session",
        lambda _client, _server: {"access_token": "platform-token"},
    )
    monkeypatch.setattr("homebrew_mlflow.cli.main._configured_server", lambda: "https://ml.example")

    result = CliRunner().invoke(
        app, ["credentials", "dvc", "--project", "pr_01K00000000000000000000000"]
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {
        "Version": 1,
        "AccessKeyId": "temporary-access",
        "SecretAccessKey": "temporary-secret",
        "SessionToken": "temporary-session",
        "Expiration": "2026-08-17T13:00:00Z",
    }
    assert result.stdout.count("\n") == 1
