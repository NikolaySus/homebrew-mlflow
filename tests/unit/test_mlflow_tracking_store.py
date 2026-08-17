from __future__ import annotations

from typing import Any

from homebrew_mlflow.mlflow_plugins.tracking_store import HomebrewTrackingStore
from mlflow.entities import Metric, Param, RunTag


class Response:
    def __init__(self, payload: dict[str, Any] | None = None) -> None:
        self._payload = payload or {}

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


def test_tracking_store_translates_pinned_mlflow_entities(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("HOMEBREW_MLFLOW_PLATFORM_INTERNAL_URL", "http://api:8000")
    monkeypatch.setenv("MLFLOW_TRACKING_TOKEN", "run-token")
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

    assert posted[0]["headers"] == {"Authorization": "Bearer run-token"}
    assert posted[0]["json"]["metrics"][0]["step"] == 2
    assert run.info.status == "RUNNING"
    assert run.data.params == {"seed": "42"}
    assert run.data.metrics == {"loss": 0.5}
