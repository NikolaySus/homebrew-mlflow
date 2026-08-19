from __future__ import annotations

import base64
import io
import json
from pathlib import Path
from typing import Any

from flask import Flask
from homebrew_mlflow.mlflow_plugins.artifacts import HomebrewArtifactRepository


class Response:
    status_code = 200

    def __init__(self, payload: dict[str, Any] | None = None, content: bytes = b"") -> None:
        self._payload = payload or {}
        self.raw = io.BytesIO(content)

    def json(self) -> dict[str, Any]:
        return self._payload

    def raise_for_status(self) -> None:
        return None


def _token() -> str:
    payload = base64.urlsafe_b64encode(json.dumps({"scp": ["read"]}).encode()).rstrip(b"=")
    return f"header.{payload.decode()}.signature"


def test_attachment_download_carries_request_auth_into_mlflow_worker(
    monkeypatch, tmp_path: Path
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("HOMEBREW_MLFLOW_PLATFORM_INTERNAL_URL", "http://api:8000")
    authorization = f"Bearer {_token()}"
    content_headers: list[dict[str, str]] = []

    def get(url: str, **kwargs: Any) -> Response:
        if url.endswith("/attachments/content"):
            content_headers.append(kwargs["headers"])
            return Response(content=b'{"accuracy":0.91}')
        return Response({"files": []})

    monkeypatch.setattr(
        "homebrew_mlflow.mlflow_plugins.artifacts.requests.get",
        get,
    )
    repository = HomebrewArtifactRepository(
        "homebrew://run_01K00000000000000000000000"
    )
    app = Flask(__name__)

    with app.test_request_context(headers={"Authorization": authorization}):
        downloaded = repository.download_artifacts(
            "reports/evaluation-summary.json", str(tmp_path)
        )

    assert Path(downloaded).read_bytes() == b'{"accuracy":0.91}'
    assert content_headers == [{"Authorization": authorization}]
