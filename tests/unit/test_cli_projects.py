from __future__ import annotations

from typing import Any

from homebrew_mlflow.cli.main import app
from typer.testing import CliRunner


class Response:
    def __init__(self, payload: object) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self._payload


class Client:
    created_json: dict[str, str] | None = None

    def __init__(self, **_kwargs: object) -> None:
        pass

    def __enter__(self) -> Client:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def get(self, url: str, **_kwargs: Any) -> Response:
        if url.endswith("/api/v1/organization"):
            return Response({"id": "org_01K00000000000000000000000", "name": "Research"})
        if url.endswith("/repositories"):
            return Response([repository("active")])
        if url.endswith("/api/v1/projects"):
            return Response(
                [
                    {
                        "id": "project_01K00000000000000000000",
                        "slug": "protein-folding",
                        "name": "Protein Folding",
                        "state": "active",
                    }
                ]
            )
        raise AssertionError(url)

    def post(self, url: str, **kwargs: Any) -> Response:
        if url.endswith("/api/v1/projects"):
            self.__class__.created_json = kwargs["json"]
            return Response(
                {
                    "id": "project_01K00000000000000000000",
                    "slug": "protein-folding",
                    "default_repository": repository("provisioning"),
                }
            )
        if url.endswith("/retry-provisioning"):
            return Response(repository("provisioning"))
        raise AssertionError(url)


def repository(state: str) -> dict[str, object]:
    return {
        "id": "repository_01K0000000000000000000",
        "state": state,
        "failure_code": "template_commit_failed" if state == "failed" else None,
        "web_url": "https://git.example/research/protein-folding",
        "ssh_clone_url": "git@git.example:research/protein-folding.git",
        "http_clone_url": "https://git.example/research/protein-folding.git",
    }


def configured(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr("homebrew_mlflow.cli.main.httpx.Client", Client)
    monkeypatch.setattr(
        "homebrew_mlflow.cli.main._refresh_session",
        lambda _client, _server: {"access_token": "platform-access"},
    )
    monkeypatch.setattr(
        "homebrew_mlflow.cli.main._configured_server", lambda: "https://ml.example"
    )


def test_project_create_suggests_slug_and_waits(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    configured(monkeypatch)

    result = CliRunner().invoke(app, ["project", "create", "--name", "Protein Folding"])

    assert result.exit_code == 0, result.output
    assert "Repository provisioning: active" in result.output
    assert "git@git.example:research/protein-folding.git" in result.output
    assert Client.created_json == {
        "organization_id": "org_01K00000000000000000000000",
        "name": "Protein Folding",
        "slug": "protein-folding",
    }


def test_project_create_no_wait_rejects_clone(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    configured(monkeypatch)

    result = CliRunner().invoke(
        app,
        [
            "project",
            "create",
            "--name",
            "Research",
            "--no-wait",
            "--clone-to",
            "research",
        ],
    )

    assert result.exit_code == 2
    assert "--clone-to cannot be combined" in result.output
