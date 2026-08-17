from __future__ import annotations

import base64
import json
import os
from collections.abc import Sequence
from typing import Any, cast

import requests
from flask import has_request_context, request
from mlflow.entities import Experiment, Metric, Param, Run, RunData, RunInfo, RunTag
from mlflow.exceptions import MlflowException
from mlflow.store.tracking.abstract_store import AbstractStore


def _unsupported(operation: str) -> MlflowException:
    return MlflowException(
        f"unsupported_operation: {operation} is not supported by Homebrew MLflow"
    )


class HomebrewTrackingStore(AbstractStore):
    """Pinned MLflow compatibility adapter backed by canonical platform APIs."""

    def __init__(self, store_uri: str, artifact_uri: str | None = None) -> None:
        super().__init__()  # type: ignore[no-untyped-call]
        self._base_url = os.environ["HOMEBREW_MLFLOW_PLATFORM_INTERNAL_URL"].rstrip("/")

    def _headers(self) -> dict[str, str]:
        if has_request_context():
            authorization = request.headers.get("Authorization")
        else:
            token = os.environ.get("MLFLOW_TRACKING_TOKEN")
            authorization = f"Bearer {token}" if token else None
        if not authorization:
            raise MlflowException("authentication_required: missing Run-scoped logging token")
        return {"Authorization": authorization}

    def _bound_run_id(self) -> str:
        authorization = self._headers()["Authorization"]
        try:
            token = authorization.removeprefix("Bearer ")
            payload_part = token.split(".")[1]
            payload = json.loads(
                base64.urlsafe_b64decode(payload_part + "=" * (-len(payload_part) % 4))
            )
            run_id = payload["run"]
            if not isinstance(run_id, str):
                raise TypeError
            return run_id
        except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise MlflowException("run_binding_required: malformed logging token") from error

    def _get(self, run_id: str) -> dict[str, Any]:
        response = requests.get(
            f"{self._base_url}/api/v1/runs/{run_id}/tracking",
            headers=self._headers(),
            timeout=30,
        )
        response.raise_for_status()
        return cast(dict[str, Any], response.json())

    def _batch(
        self,
        run_id: str,
        *,
        metrics: Sequence[Metric] = (),
        params: Sequence[Param] = (),
        tags: Sequence[RunTag] = (),
    ) -> None:
        response = requests.post(
            f"{self._base_url}/api/v1/runs/{run_id}/tracking/batch",
            headers=self._headers(),
            json={
                "parameters": [{"key": item.key, "value": item.value} for item in params],
                "metrics": [
                    {
                        "key": item.key,
                        "value": item.value,
                        "timestamp_ms": item.timestamp,
                        "step": item.step,
                    }
                    for item in metrics
                ],
                "tags": [{"key": item.key, "value": item.value} for item in tags],
            },
            timeout=30,
        )
        response.raise_for_status()

    def get_run(self, run_id: str) -> Run:
        payload = self._get(run_id)
        metrics: dict[str, Metric] = {}
        for item in payload["metrics"]:
            candidate = Metric(item["key"], item["value"], item["timestamp_ms"], item["step"])
            previous = metrics.get(candidate.key)
            if previous is None or (candidate.step, candidate.timestamp) >= (
                previous.step,
                previous.timestamp,
            ):
                metrics[candidate.key] = candidate
        return Run(
            RunInfo(  # type: ignore[no-untyped-call]
                payload["id"],
                payload["experiment_id"],
                "homebrew-mlflow",
                self._status(payload["state"]),
                self._milliseconds(payload["started_at"]),
                self._milliseconds(payload["ended_at"]),
                "active",
                payload["attachment_uri"],
                payload["id"],
            ),
            RunData(  # type: ignore[no-untyped-call]
                metrics=list(metrics.values()),
                params=[
                    Param(item["key"], item["value"])  # type: ignore[no-untyped-call]
                    for item in payload["parameters"]
                ],
                tags=[
                    RunTag(item["key"], item["value"])  # type: ignore[no-untyped-call]
                    for item in payload["tags"]
                ],
            ),
        )

    @staticmethod
    def _milliseconds(value: str | None) -> int | None:
        if value is None:
            return None
        from datetime import datetime

        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)

    @staticmethod
    def _status(state: str) -> str:
        return {
            "created": "SCHEDULED",
            "running": "RUNNING",
            "finalizing": "RUNNING",
            "succeeded": "FINISHED",
            "failed": "FAILED",
            "interrupted": "KILLED",
            "incomplete": "FAILED",
        }[state]

    def log_batch(
        self,
        run_id: str,
        metrics: list[Metric],
        params: list[Param],
        tags: list[RunTag],
    ) -> None:
        self._batch(run_id, metrics=metrics, params=params, tags=tags)

    def log_metric(self, run_id: str, metric: Metric) -> None:
        self._batch(run_id, metrics=(metric,))

    def log_param(self, run_id: str, param: Param) -> None:
        self._batch(run_id, params=(param,))

    def set_tag(self, run_id: str, tag: RunTag) -> None:
        self._batch(run_id, tags=(tag,))

    def get_metric_history(
        self,
        run_id: str,
        metric_key: str,
        max_results: int | None = None,
        page_token: str | None = None,
    ) -> list[Metric]:
        if page_token is not None:
            raise _unsupported("metric history pagination")
        values = [
            Metric(item["key"], item["value"], item["timestamp_ms"], item["step"])
            for item in self._get(run_id)["metrics"]
            if item["key"] == metric_key
        ]
        return values[:max_results] if max_results is not None else values

    def update_run_info(
        self,
        run_id: str,
        run_status: str,
        end_time: int | None,
        run_name: str | None,
    ) -> RunInfo:
        # The local coordinator owns heartbeat and terminal evidence. MLflow end_run is a
        # compatibility no-op; the coordinator finalizes immediately after the child exits.
        return self.get_run(run_id).info

    def get_experiment(self, experiment_id: str) -> Experiment:
        run_id = self._bound_run_id()
        if self._get(run_id)["experiment_id"] != experiment_id:
            raise MlflowException("RESOURCE_DOES_NOT_EXIST: experiment is outside this Run")
        return Experiment(  # type: ignore[no-untyped-call]
            experiment_id,
            "Homebrew MLflow Experiment",
            f"homebrew://{run_id}",
            "active",
        )

    def create_experiment(self, name: str, artifact_location: str | None, tags: Any) -> str:
        raise _unsupported("create_experiment; use homebrew-mlflow run --experiment")

    def create_run(
        self, experiment_id: str, user_id: str, start_time: int, tags: Any, run_name: str
    ) -> Run:
        raise _unsupported("create_run; use homebrew-mlflow run")

    def delete_run(self, run_id: str) -> None:
        raise _unsupported("delete_run")

    def restore_run(self, run_id: str) -> None:
        raise _unsupported("restore_run")
