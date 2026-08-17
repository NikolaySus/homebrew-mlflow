from __future__ import annotations

import mimetypes
import os
import shutil
from pathlib import Path
from urllib.parse import urlparse

import requests
from mlflow.entities import FileInfo
from mlflow.exceptions import MlflowException
from mlflow.store.artifact.artifact_repo import ArtifactRepository

_MAX_ATTACHMENT_BYTES = 50 * 1024 * 1024


class HomebrewArtifactRepository(ArtifactRepository):
    def __init__(
        self,
        artifact_uri: str,
        tracking_uri: str | None = None,
        registry_uri: str | None = None,
    ) -> None:
        super().__init__(artifact_uri, tracking_uri, registry_uri)
        parsed = urlparse(artifact_uri)
        self._run_id = parsed.netloc or parsed.path.lstrip("/")
        self._base_url = os.environ["HOMEBREW_MLFLOW_SERVER"].rstrip("/")
        self._token = os.environ["MLFLOW_TRACKING_TOKEN"]

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    def log_artifact(self, local_file: str, artifact_path: str | None = None) -> None:
        source = Path(local_file)
        if source.stat().st_size > _MAX_ATTACHMENT_BYTES:
            raise MlflowException("Run attachment exceeds the 50 MiB policy limit")
        relative = Path(artifact_path or "") / source.name
        media_type = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
        with source.open("rb") as stream:
            response = requests.post(
                f"{self._base_url}/api/v1/runs/{self._run_id}/attachments",
                headers=self._headers(),
                data={"path": relative.as_posix()},
                files={"file": (source.name, stream, media_type)},
                timeout=60,
            )
        response.raise_for_status()

    def log_artifacts(self, local_dir: str, artifact_path: str | None = None) -> None:
        root = Path(local_dir)
        for source in root.rglob("*"):
            if source.is_file():
                relative_parent = source.relative_to(root).parent
                target = Path(artifact_path or "") / relative_parent
                self.log_artifact(str(source), target.as_posix())

    def list_artifacts(self, path: str | None = None) -> list[FileInfo]:
        response = requests.get(
            f"{self._base_url}/api/v1/runs/{self._run_id}/attachments",
            headers=self._headers(),
            params={"path": path or ""},
            timeout=30,
        )
        response.raise_for_status()
        return [FileInfo(**item) for item in response.json()["files"]]  # type: ignore[no-untyped-call]

    def _download_file(self, remote_file_path: str, local_path: str) -> None:
        response = requests.get(
            f"{self._base_url}/api/v1/runs/{self._run_id}/attachments/content",
            headers=self._headers(),
            params={"path": remote_file_path},
            stream=True,
            timeout=60,
        )
        response.raise_for_status()
        with Path(local_path).open("wb") as target:
            shutil.copyfileobj(response.raw, target)
