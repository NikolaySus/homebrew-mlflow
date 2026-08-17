from __future__ import annotations

from urllib.parse import quote

import httpx
from homebrew_mlflow.domain import PublicId, normalize_artifact_path
from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import GitRepositoryRow


class GitLabPipelineSourceReader:
    """Read a pipeline definition from an exact committed GitLab snapshot."""

    def __init__(self, session: Session, base_url: str, access_token: str) -> None:
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._headers = {"PRIVATE-TOKEN": access_token}

    def read(self, repository_id: PublicId, commit: str, path: str) -> bytes:
        provider_id = self._session.scalar(
            select(GitRepositoryRow.provider_id).where(
                GitRepositoryRow.public_id == str(repository_id),
                GitRepositoryRow.state == "active",
            )
        )
        if provider_id is None:
            raise ValueError("active Repository does not exist")
        safe_path = normalize_artifact_path(path)
        response = httpx.get(
            f"{self._base_url}/api/v4/projects/{quote(provider_id, safe='')}/repository/"
            f"files/{quote(safe_path, safe='')}/raw",
            headers=self._headers,
            params={"ref": commit},
            timeout=20,
        )
        if response.status_code == 404:
            raise ValueError("pipeline source does not exist at the requested commit")
        response.raise_for_status()
        return response.content
