from __future__ import annotations

from dataclasses import dataclass

import httpx
from homebrew_mlflow.application import (
    HostedRepository,
    HostedRepositoryRequest,
    RepositorySeedFile,
)


@dataclass(frozen=True, slots=True)
class GitLabRepositoryProvisioningError(RuntimeError):
    message: str
    provider_id: str | None = None

    def __str__(self) -> str:
        if self.provider_id is None:
            return self.message
        return f"{self.message} (GitLab project {self.provider_id})"


class GitLabRepositoryHost:
    def __init__(
        self,
        base_url: str,
        access_token: str,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=30)
        self._headers = {"PRIVATE-TOKEN": access_token}

    def create_with_seed(
        self,
        request: HostedRepositoryRequest,
        files: tuple[RepositorySeedFile, ...],
    ) -> HostedRepository:
        readme = next((file for file in files if file.path == "README.md"), None)
        if readme is None:
            raise GitLabRepositoryProvisioningError(
                "repository template must contain README.md to initialize the default branch"
            )

        response = self._client.post(
            f"{self._base_url}/api/v4/projects",
            headers=self._headers,
            json={
                "name": request.name,
                "path": request.slug,
                "namespace_id": request.namespace_id,
                "default_branch": request.default_branch,
                "initialize_with_readme": True,
            },
        )
        response.raise_for_status()
        payload = response.json()
        try:
            provider_id = str(payload["id"])
            default_branch = str(payload.get("default_branch") or request.default_branch)
            hosted = HostedRepository(
                provider_id=provider_id,
                default_branch=default_branch,
                web_url=str(payload["web_url"]),
                http_clone_url=str(payload["http_url_to_repo"]),
                ssh_clone_url=str(payload["ssh_url_to_repo"]),
            )
        except (KeyError, TypeError) as error:
            raise GitLabRepositoryProvisioningError(
                "GitLab returned an invalid project response"
            ) from error

        actions = [
            {
                "action": "update" if file.path == "README.md" else "create",
                "file_path": file.path,
                "content": file.content,
                "execute_filemode": file.executable,
            }
            for file in files
        ]
        try:
            commit = self._client.post(
                f"{self._base_url}/api/v4/projects/{provider_id}/repository/commits",
                headers=self._headers,
                json={
                    "branch": default_branch,
                    "commit_message": "Initialize Homebrew MLflow research repository",
                    "actions": actions,
                },
            )
            commit.raise_for_status()
        except httpx.HTTPError as error:
            raise GitLabRepositoryProvisioningError(
                "GitLab project was created but its research template could not be committed",
                provider_id=provider_id,
            ) from error
        return hosted
