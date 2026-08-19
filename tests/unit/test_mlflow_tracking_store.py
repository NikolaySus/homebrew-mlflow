from __future__ import annotations

import base64
import json
from typing import Any

import pytest
from flask import Flask
from homebrew_mlflow.mlflow_plugins.request_auth import HomebrewTokenFileAuthProvider
from homebrew_mlflow.mlflow_plugins.tracking_store import HomebrewTrackingStore
from mlflow.entities import Metric, Param, RunTag
from mlflow.exceptions import MlflowException
from mlflow.utils.workspace_context import (
    clear_server_request_workspace,
    set_server_request_workspace,
)
from requests import Request


class Response:
    def __init__(self, payload: Any = None) -> None:
        self._payload = payload or {}
        self.ok = True
        self.status_code = 200

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        return self._payload


def _token(payload: dict[str, Any]) -> str:
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"header.{encoded}.signature"


def test_tracking_store_translates_pinned_mlflow_entities(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("HOMEBREW_MLFLOW_PLATFORM_INTERNAL_URL", "http://api:8000")
    monkeypatch.setenv("MLFLOW_TRACKING_TOKEN", "header.payload.signature")
    posted: list[dict[str, Any]] = []

    def post(_url: str, **kwargs: Any) -> Response:
        posted.append(kwargs)
        return Response()

    def get(_url: str, **_kwargs: Any) -> Response:
        return Response(
            {
                "id": "run_01K00000000000000000000000",
                "experiment_id": "exp_01K00000000000000000000000",
                "project_id": "pr_01K00000000000000000000000",
                "state": "running",
                "started_at": "2026-08-17T12:00:00Z",
                "ended_at": None,
                "attachment_uri": "homebrew://run_01K00000000000000000000000",
                "parameters": [{"key": "seed", "value": "42"}],
                "metrics": [{"key": "loss", "value": 0.5, "timestamp_ms": 1000, "step": 1}],
                "tags": [{"key": "model", "value": "resnet"}],
            }
        )

    monkeypatch.setattr("homebrew_mlflow.mlflow_plugins.tracking_store.requests.post", post)
    monkeypatch.setattr("homebrew_mlflow.mlflow_plugins.tracking_store.requests.get", get)
    store = HomebrewTrackingStore("homebrew://platform")

    store.log_batch(
        "run_01K00000000000000000000000",
        [Metric("loss", 0.25, 2000, 2)],
        [Param("seed", "42")],  # type: ignore[no-untyped-call]
        [RunTag("model", "resnet")],  # type: ignore[no-untyped-call]
    )
    run = store.get_run("run_01K00000000000000000000000")

    assert posted[0]["headers"] == {"Authorization": "Bearer header.payload.signature"}
    assert posted[0]["json"]["metrics"][0]["step"] == 2
    assert run.info.status == "RUNNING"
    assert run.data.params == {"seed": "42"}
    assert run.data.metrics == {"loss": 0.5}


def test_request_auth_reloads_rotating_token_file(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    token_file = tmp_path / "token"
    token_file.write_text("first", encoding="utf-8")
    monkeypatch.setenv("MLFLOW_TRACKING_TOKEN_FILE", str(token_file))
    auth = HomebrewTokenFileAuthProvider().get_auth()

    first = auth(Request("GET", "https://ml.example/mlflow").prepare())
    token_file.write_text("second", encoding="utf-8")
    second = auth(Request("GET", "https://ml.example/mlflow").prepare())

    assert first.headers["Authorization"] == "Bearer first"
    assert second.headers["Authorization"] == "Bearer second"


def test_tracking_store_missing_request_auth_is_unauthorized(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("HOMEBREW_MLFLOW_PLATFORM_INTERNAL_URL", "http://api:8000")
    monkeypatch.delenv("MLFLOW_TRACKING_TOKEN", raising=False)
    monkeypatch.delenv("MLFLOW_TRACKING_TOKEN_FILE", raising=False)

    with pytest.raises(MlflowException) as caught:
        HomebrewTrackingStore("homebrew://platform").get_run("run_test")

    assert caught.value.error_code == "CUSTOMER_UNAUTHORIZED"


@pytest.mark.parametrize(
    "operation",
    ["search_mcp_servers", "list_gateway_endpoints", "list_endpoint_bindings"],
)
def test_tracking_store_reports_unsupported_native_surfaces(
    operation: str, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("HOMEBREW_MLFLOW_PLATFORM_INTERNAL_URL", "http://api:8000")
    store = HomebrewTrackingStore("homebrew://platform")

    with pytest.raises(MlflowException) as caught:
        getattr(store, operation)()

    assert caught.value.error_code == "INVALID_PARAMETER_VALUE"
    assert "unsupported_operation" in str(caught.value)


def test_tracking_store_searches_browser_workspace(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from mlflow.server.handlers import _search_runs

    monkeypatch.setenv("HOMEBREW_MLFLOW_PLATFORM_INTERNAL_URL", "http://api:8000")
    workspace = "pr-01k00000000000000000000000"
    payload = {
        "workspace": {"name": workspace},
        "experiments": [
            {
                "id": "exp_01K00000000000000000000000",
                "name": "baseline",
                "created_at": "2026-08-17T12:00:00Z",
                "archived_at": None,
                "last_update_at": "2026-08-17T13:00:00Z",
            }
        ],
        "runs": [
            {
                "id": "run_01K00000000000000000000000",
                "experiment_id": "exp_01K00000000000000000000000",
                "creator_principal_id": "principal_01K00000000000000000000000",
                "state": "succeeded",
                "created_at": "2026-08-17T12:00:00Z",
                "started_at": "2026-08-17T12:00:00Z",
                "ended_at": "2026-08-17T13:00:00Z",
                "attachment_uri": "homebrew://run_01K00000000000000000000000",
                "parameters": [{"key": "seed", "value": "42"}],
                "metrics": [{"key": "loss", "value": 0.25, "timestamp_ms": 2, "step": 1}],
                "tags": [],
                "input_artifact_version_ids": ["av_dataset"],
                "output_artifact_version_ids": ["av_model"],
            }
        ],
    }

    catalog = {
        "artifacts": [
            {
                "id": "ar_dataset",
                "name": "training-data",
                "kind": "dataset",
                "versions": [
                    {
                        "id": "av_dataset",
                        "algorithm": "md5",
                        "digest": "a" * 32,
                    }
                ],
                "aliases": [],
            },
            {
                "id": "ar_model",
                "name": "ranker",
                "kind": "model",
                "versions": [
                    {"id": "av_model", "mlflow_model_id": "m-0123456789abcdef"}
                ],
                "aliases": [],
            },
        ]
    }
    monkeypatch.setattr(
        "homebrew_mlflow.mlflow_plugins.tracking_store.requests.get",
        lambda url, **_kwargs: Response(catalog if url.endswith("/catalog") else payload),
    )
    app = Flask(__name__)
    authorization = _token(
        {"scp": ["read"], "prj": "pr_01K00000000000000000000000"}
    )
    store = HomebrewTrackingStore("homebrew://platform")
    monkeypatch.setattr("mlflow.server.handlers._get_tracking_store", lambda: store)
    with app.test_request_context(
        "/ajax-api/2.0/mlflow/runs/search",
        method="POST",
        json={
            "experiment_ids": ["exp_01K00000000000000000000000"],
            "run_view_type": "ACTIVE_ONLY",
            "max_results": 10,
        },
        headers={"Authorization": f"Bearer {authorization}"},
    ):
        set_server_request_workspace(workspace)
        try:
            experiments = store.search_experiments(
                max_results=25,
                filter_string="tags.`mlflow.experiment.isGateway` IS NULL",
                order_by=["last_update_time DESC"],
            )
            runs = store.search_runs(
                ["exp_01K00000000000000000000000"], None, 1, max_results=10
            )
            datasets = store._search_datasets(
                ["exp_01K00000000000000000000000"]
            )
            search_response = _search_runs()
        finally:
            clear_server_request_workspace()
    assert [item.name for item in experiments] == ["baseline"]
    assert runs[0].data.metrics == {"loss": 0.25}
    assert runs[0].data.params == {"seed": "42"}
    assert runs[0].data.tags["mlflow.runName"] == "run_01K00000000000000000000000"
    assert (
        runs[0].data.tags["mlflow.user"]
        == "principal_01K00000000000000000000000"
    )
    serialized_tags = search_response.get_json()["runs"][0]["data"]["tags"]
    assert {item["key"] for item in serialized_tags} == {
        "mlflow.runName",
        "mlflow.user",
    }
    assert runs[0].inputs.dataset_inputs[0].dataset.name == "training-data"
    assert runs[0].inputs.dataset_inputs[0].dataset.digest == f"md5:{'a' * 32}"
    assert runs[0].outputs.model_outputs[0].model_id == "m-0123456789abcdef"
    assert [item.to_dict() for item in datasets] == [
        {
            "experiment_id": "exp_01K00000000000000000000000",
            "name": "training-data",
            "digest": f"md5:{'a' * 32}",
            "context": None,
        }
    ]


def test_mlflow_search_datasets_handler_serializes_dvc_summaries(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from mlflow.server.handlers import _search_datasets_handler

    monkeypatch.setenv("HOMEBREW_MLFLOW_PLATFORM_INTERNAL_URL", "http://api:8000")
    workspace = "pr-01k00000000000000000000000"
    experiment_id = "exp_01K00000000000000000000000"
    snapshot = {
        "runs": [
            {
                "experiment_id": experiment_id,
                "input_artifact_version_ids": ["av_dataset"],
            }
        ]
    }
    catalog = {
        "artifacts": [
            {
                "name": "training-data",
                "kind": "dataset",
                "versions": [
                    {
                        "id": "av_dataset",
                        "algorithm": "md5",
                        "digest": "a" * 32,
                    }
                ],
            }
        ]
    }
    monkeypatch.setattr(
        "homebrew_mlflow.mlflow_plugins.tracking_store.requests.get",
        lambda url, **_kwargs: Response(catalog if url.endswith("/catalog") else snapshot),
    )
    store = HomebrewTrackingStore("homebrew://platform")
    monkeypatch.setattr("mlflow.server.handlers._get_tracking_store", lambda: store)
    authorization = _token(
        {"scp": ["read"], "prj": "pr_01K00000000000000000000000"}
    )
    app = Flask(__name__)
    with app.test_request_context(
        "/ajax-api/2.0/mlflow/experiments/search-datasets",
        method="POST",
        json={"experiment_ids": [experiment_id]},
        headers={"Authorization": f"Bearer {authorization}"},
    ):
        set_server_request_workspace(workspace)
        try:
            response = _search_datasets_handler()
        finally:
            clear_server_request_workspace()

    assert response.status_code == 200
    assert response.get_json() == {
        "dataset_summaries": [
            {
                "experiment_id": experiment_id,
                "name": "training-data",
                "digest": f"md5:{'a' * 32}",
            }
        ]
    }
