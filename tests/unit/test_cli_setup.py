from __future__ import annotations

from typing import Any

from homebrew_mlflow.cli.main import app
from typer.testing import CliRunner


class Response:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, str]:
        return {
            "organization_id": "org_01K00000000000000000000000",
            "principal_id": "principal_01K00000000000000000000000",
            "role": "admin",
        }


class Client:
    request: tuple[str, dict[str, Any]] | None = None

    def __init__(self, **_kwargs: object) -> None:
        pass

    def __enter__(self) -> Client:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def post(self, url: str, **kwargs: Any) -> Response:
        self.__class__.request = (url, kwargs)
        return Response()


def test_claim_installation_prompts_for_secret_without_echo(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr("homebrew_mlflow.cli.main.httpx.Client", Client)
    monkeypatch.setattr(
        "homebrew_mlflow.cli.main._refresh_session",
        lambda _client, _server: {"access_token": "platform-access"},
    )
    monkeypatch.setattr("homebrew_mlflow.cli.main._configured_server", lambda: "https://ml.example")

    result = CliRunner().invoke(
        app,
        ["claim-installation", "--organization", "Research"],
        input="one-time-bootstrap-secret\n",
    )

    assert result.exit_code == 0, result.output
    assert "one-time-bootstrap-secret" not in result.output
    assert "organization=org_01K00000000000000000000000" in result.output
    assert Client.request == (
        "https://ml.example/api/v1/setup/claim",
        {
            "headers": {"Authorization": "Bearer platform-access"},
            "json": {
                "organization_name": "Research",
                "bootstrap_token": "one-time-bootstrap-secret",
            },
        },
    )
