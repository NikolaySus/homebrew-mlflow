from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

import requests
import yaml
from mlflow.entities import FileInfo
from mlflow.exceptions import MlflowException
from mlflow.store.artifact.artifact_repo import ArtifactRepository

from .auth_context import authorization_header


class HomebrewModelArtifactRepository(ArtifactRepository):
    """Virtual repository exposing only platform-synthesized model metadata."""

    def __init__(
        self,
        artifact_uri: str,
        tracking_uri: str | None = None,
        registry_uri: str | None = None,
    ) -> None:
        super().__init__(artifact_uri, tracking_uri, registry_uri)
        parsed = urlparse(artifact_uri)
        self._workspace = parsed.netloc
        self._version_id = parsed.path.strip("/")
        if not self._workspace or not self._version_id.startswith("av_"):
            raise MlflowException("invalid Homebrew model metadata URI")
        self._base_url = os.environ["HOMEBREW_MLFLOW_PLATFORM_INTERNAL_URL"].rstrip("/")
        self._download_lock = threading.Lock()
        self._active_download_headers: dict[str, str] | None = None

    def _mlmodel(self, headers: dict[str, str]) -> bytes:
        response = requests.get(
            f"{self._base_url}/api/v1/mlflow/workspaces/{self._workspace}/catalog",
            headers=headers,
            timeout=30,
        )
        response.raise_for_status()
        payload = cast(dict[str, Any], response.json())
        artifact: dict[str, Any] | None = None
        version: dict[str, Any] | None = None
        for candidate in payload.get("artifacts", []):
            match = next(
                (
                    item
                    for item in candidate.get("versions", [])
                    if item.get("id") == self._version_id
                ),
                None,
            )
            if match is not None:
                artifact, version = candidate, match
                break
        if artifact is None or version is None:
            raise MlflowException("model Artifact Version does not exist")
        document: dict[str, Any] = {
            "artifact_path": artifact["name"],
            "flavors": {
                "homebrew_dvc": {
                    "artifact_uri": f"homebrew-dvc://{self._version_id}",
                    "artifact_version_id": self._version_id,
                    "digest": f"{version['algorithm']}:{version['digest']}",
                }
            },
            "model_uuid": version["mlflow_model_id"],
            "run_id": version.get("producing_run_id"),
        }
        signature = version.get("model_signature")
        if signature is not None:
            document["signature"] = {
                "inputs": json.dumps(signature["inputs"], separators=(",", ":")),
                "outputs": json.dumps(signature["outputs"], separators=(",", ":")),
            }
        return yaml.safe_dump(document, sort_keys=False).encode("utf-8")

    def log_artifact(self, local_file: str, artifact_path: str | None = None) -> None:
        raise MlflowException("Homebrew model metadata is read-only")

    def log_artifacts(self, local_dir: str, artifact_path: str | None = None) -> None:
        raise MlflowException("Homebrew model metadata is read-only")

    def list_artifacts(self, path: str | None = None) -> list[FileInfo]:
        if path:
            return []
        content = self._mlmodel(authorization_header())
        return [FileInfo("MLmodel", False, len(content))]  # type: ignore[no-untyped-call]

    def download_artifacts(self, artifact_path: str, dst_path: str | None = None) -> str:
        headers = authorization_header()
        with self._download_lock:
            self._active_download_headers = headers
            try:
                return super().download_artifacts(artifact_path, dst_path)
            finally:
                self._active_download_headers = None

    def _download_file(self, remote_file_path: str, local_path: str) -> None:
        if remote_file_path != "MLmodel":
            raise MlflowException("only synthesized MLmodel metadata is available")
        content = self._mlmodel(self._active_download_headers or authorization_header())
        Path(local_path).write_bytes(content)
