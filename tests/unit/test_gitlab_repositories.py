from __future__ import annotations

import json

import httpx
import pytest
from homebrew_mlflow.application import HostedRepositoryRequest, RepositorySeedFile
from homebrew_mlflow.infrastructure import (
    GitLabRepositoryHost,
    GitLabRepositoryProvisioningError,
)


def test_gitlab_project_is_created_and_seeded_with_one_template_commit() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/v4/projects":
            return httpx.Response(
                201,
                request=request,
                json={
                    "id": 42,
                    "default_branch": "main",
                    "web_url": "https://git.example/group/repo",
                    "http_url_to_repo": "https://git.example/group/repo.git",
                    "ssh_url_to_repo": "git@git.example:group/repo.git",
                },
            )
        return httpx.Response(201, request=request, json={"id": "commit-sha"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    host = GitLabRepositoryHost("https://git.example", "secret", client=client)
    files = (
        RepositorySeedFile("README.md", "# Research\n"),
        RepositorySeedFile("scripts/run.py", "print('run')\n", executable=True),
    )

    result = host.create_with_seed(
        HostedRepositoryRequest(9, "Research", "research", "main"), files
    )

    assert result.provider_id == "42"
    create_payload = json.loads(requests[0].content)
    assert create_payload == {
        "name": "Research",
        "path": "research",
        "namespace_id": 9,
        "default_branch": "main",
        "initialize_with_readme": True,
    }
    commit_payload = json.loads(requests[1].content)
    assert commit_payload["branch"] == "main"
    assert commit_payload["actions"] == [
        {
            "action": "update",
            "file_path": "README.md",
            "content": "# Research\n",
            "execute_filemode": False,
        },
        {
            "action": "create",
            "file_path": "scripts/run.py",
            "content": "print('run')\n",
            "execute_filemode": True,
        },
    ]
    assert requests[0].headers["PRIVATE-TOKEN"] == "secret"


def test_seed_failure_reports_created_gitlab_project() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v4/projects":
            return httpx.Response(
                201,
                request=request,
                json={
                    "id": 42,
                    "default_branch": "main",
                    "web_url": "https://git/repo",
                    "http_url_to_repo": "https://git/repo.git",
                    "ssh_url_to_repo": "git@git:repo.git",
                },
            )
        return httpx.Response(500, request=request)

    host = GitLabRepositoryHost(
        "https://git.example",
        "secret",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(GitLabRepositoryProvisioningError, match="GitLab project 42"):
        host.create_with_seed(
            HostedRepositoryRequest(9, "Research", "research", "main"),
            (RepositorySeedFile("README.md", "# Research\n"),),
        )


def test_retry_recognizes_matching_seeded_repository() -> None:
    requests: list[httpx.Request] = []
    sentinel = '{"project_id":"project_1"}\n'

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/v4/projects/42":
            return httpx.Response(
                200,
                request=request,
                json={
                    "id": 42,
                    "default_branch": "main",
                    "web_url": "https://git/repo",
                    "http_url_to_repo": "https://git/repo.git",
                    "ssh_url_to_repo": "git@git:repo.git",
                },
            )
        if request.url.path.endswith("/.homebrew-mlflow.json/raw"):
            return httpx.Response(200, request=request, text=sentinel)
        raise AssertionError(request.url)

    host = GitLabRepositoryHost(
        "https://git.example",
        "secret",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    result = host.create_with_seed(
        HostedRepositoryRequest(9, "Research", "research", "main", provider_id="42"),
        (
            RepositorySeedFile("README.md", "# Research\n"),
            RepositorySeedFile(".homebrew-mlflow.json", sentinel),
        ),
    )

    assert result.provider_id == "42"
    assert [request.method for request in requests] == ["GET", "GET"]
