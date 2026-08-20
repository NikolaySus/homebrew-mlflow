from __future__ import annotations

import base64
import json
from typing import Any

from flask import Flask
from homebrew_mlflow.mlflow_plugins.model_registry_store import HomebrewModelRegistryStore
from mlflow.utils.workspace_context import (
    clear_server_request_workspace,
    set_server_request_workspace,
)


class Response:
    ok = True
    status_code = 200

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def json(self) -> dict[str, Any]:
        return self._payload


def _token() -> str:
    payload = base64.urlsafe_b64encode(
        json.dumps({"scp": ["read"], "prj": "pr_01K00000000000000000000000"}).encode()
    ).rstrip(b"=").decode()
    return f"header.{payload}.signature"


def test_model_registry_is_a_read_only_view_of_model_artifacts(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("HOMEBREW_MLFLOW_PLATFORM_INTERNAL_URL", "http://api:8000")
    payload = {
        "artifacts": [
            {
                "id": "ar_01K00000000000000000000000",
                "name": "ranker",
                "kind": "model",
                "description": "Published ranker",
                "created_at": "2026-08-17T12:00:00Z",
                "aliases": [
                    {
                        "alias": "champion",
                        "artifact_version_id": "av_01K00000000000000000000000",
                    }
                ],
                "versions": [
                    {
                        "id": "av_01K00000000000000000000000",
                        "sequence": 1,
                        "algorithm": "md5",
                        "digest": "a" * 32,
                        "published_at": "2026-08-17T13:00:00Z",
                        "mlflow_model_id": "m-0123456789abcdef0123456789abcdef",
                        "producing_run_id": "run_01K00000000000000000000000",
                    }
                ],
            },
            {
                "id": "ar_01K00000000000000000000001",
                "name": "raw-data",
                "kind": "dataset",
                "description": None,
                "created_at": "2026-08-17T12:00:00Z",
                "aliases": [],
                "versions": [],
            },
        ]
    }
    snapshot = {
        "workspace": {"project_id": "pr_01K00000000000000000000000"},
        "runs": [
            {
                "id": "run_01K00000000000000000000000",
                "experiment_id": "exp_01K00000000000000000000000",
                "input_artifact_version_ids": [],
                "output_artifact_version_ids": ["av_01K00000000000000000000000"],
            }
        ],
    }
    monkeypatch.setattr(
        "homebrew_mlflow.mlflow_plugins.model_registry_store.requests.get",
        lambda url, **_kwargs: Response(snapshot if url.endswith("/snapshot") else payload),
    )
    workspace = "pr-01k00000000000000000000000"
    app = Flask(__name__)
    with app.test_request_context(headers={"Authorization": f"Bearer {_token()}"}):
        set_server_request_workspace(workspace)
        try:
            store = HomebrewModelRegistryStore("homebrew://platform")
            models = store.search_registered_models()
            version = store.get_model_version_by_alias("ranker", "champion")
            download_uri = store.get_model_version_download_uri("ranker", "1")
        finally:
            clear_server_request_workspace()

    assert [model.name for model in models] == ["ranker"]
    assert models[0].aliases == {"champion": "1"}
    assert version.version == "1"
    assert version.source == "homebrew-dvc://av_01K00000000000000000000000"
    provenance = json.loads(version.tags["homebrew.provenance"])
    assert provenance["current"]["id"] == "av_01K00000000000000000000000"
    assert provenance["run_id"] == "run_01K00000000000000000000000"
    assert download_uri == (
        "homebrew-model://pr-01k00000000000000000000000/"
        "av_01K00000000000000000000000"
    )
