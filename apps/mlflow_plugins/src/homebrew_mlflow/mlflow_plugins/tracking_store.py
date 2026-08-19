from __future__ import annotations

import json
import os
from collections.abc import Sequence
from typing import Any, cast

import requests
from mlflow.entities import (
    Dataset,
    DatasetInput,
    Experiment,
    InputTag,
    LoggedModel,
    LoggedModelOutput,
    Metric,
    Param,
    Run,
    RunData,
    RunInfo,
    RunInputs,
    RunOutputs,
    RunTag,
    ViewType,
)
from mlflow.entities.dataset_summary import _DatasetSummary
from mlflow.exceptions import MlflowException
from mlflow.protos.databricks_pb2 import (
    CUSTOMER_UNAUTHORIZED,
    INVALID_PARAMETER_VALUE,
    PERMISSION_DENIED,
    RESOURCE_DOES_NOT_EXIST,
)
from mlflow.store.entities.paged_list import PagedList
from mlflow.store.tracking.abstract_store import AbstractStore
from mlflow.utils.search_utils import (
    SearchExperimentsUtils,
    SearchLoggedModelsUtils,
    SearchUtils,
)
from mlflow.utils.workspace_context import get_request_workspace

from .auth_context import authorization_header, token_claims


def _unsupported(operation: str) -> MlflowException:
    return MlflowException(
        f"unsupported_operation: {operation} is not supported by Homebrew MLflow",
        error_code=INVALID_PARAMETER_VALUE,
    )


def _platform_error(response: requests.Response) -> MlflowException:
    error_code = {
        400: INVALID_PARAMETER_VALUE,
        401: CUSTOMER_UNAUTHORIZED,
        403: PERMISSION_DENIED,
        404: RESOURCE_DOES_NOT_EXIST,
    }.get(response.status_code)
    if error_code is None:
        return MlflowException(
            f"platform_request_failed: status={response.status_code}",
        )
    return MlflowException(
        f"platform_request_failed: status={response.status_code}",
        error_code=error_code,
    )


class HomebrewTrackingStore(AbstractStore):
    """Pinned MLflow compatibility adapter backed by canonical platform APIs."""

    supports_workspaces = True

    def __init__(self, store_uri: str, artifact_uri: str | None = None) -> None:
        super().__init__()  # type: ignore[no-untyped-call]
        self._base_url = os.environ["HOMEBREW_MLFLOW_PLATFORM_INTERNAL_URL"].rstrip("/")

    def _headers(self) -> dict[str, str]:
        return authorization_header()

    def _bound_run_id(self) -> str:
        try:
            run_id = token_claims()["run"]
            if not isinstance(run_id, str):
                raise TypeError
            return run_id
        except (KeyError, TypeError) as error:
            raise MlflowException(
                "run_binding_required: malformed logging token",
                error_code=CUSTOMER_UNAUTHORIZED,
            ) from error

    def _read_snapshot(self) -> dict[str, Any]:
        workspace = get_request_workspace()
        if not workspace:
            project_id = token_claims().get("prj")
            if not isinstance(project_id, str):
                raise MlflowException.invalid_parameter_value("active workspace is required")
            workspace = project_id.replace("pr_", "pr-", 1).lower()
        response = requests.get(
            f"{self._base_url}/api/v1/mlflow/workspaces/{workspace}/snapshot",
            headers=self._headers(),
            timeout=30,
        )
        if not getattr(response, "ok", True):
            raise _platform_error(response)
        return cast(dict[str, Any], response.json())

    def _read_catalog(self) -> dict[str, Any]:
        workspace = get_request_workspace()
        if not workspace:
            project_id = token_claims().get("prj")
            if not isinstance(project_id, str):
                raise MlflowException.invalid_parameter_value("active workspace is required")
            workspace = project_id.replace("pr_", "pr-", 1).lower()
        response = requests.get(
            f"{self._base_url}/api/v1/mlflow/workspaces/{workspace}/catalog",
            headers=self._headers(),
            timeout=30,
        )
        if not getattr(response, "ok", True):
            raise _platform_error(response)
        return cast(dict[str, Any], response.json())

    @staticmethod
    def _read_token() -> bool:
        try:
            return "read" in token_claims().get("scp", [])
        except MlflowException:
            return False

    def _get(self, run_id: str) -> dict[str, Any]:
        response = requests.get(
            f"{self._base_url}/api/v1/runs/{run_id}/tracking",
            headers=self._headers(),
            timeout=30,
        )
        if not getattr(response, "ok", True):
            raise _platform_error(response)
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
        if not getattr(response, "ok", True):
            raise _platform_error(response)

    def get_run(self, run_id: str) -> Run:
        if self._read_token():
            payload = next(
                (item for item in self._read_snapshot()["runs"] if item["id"] == run_id),
                None,
            )
            if payload is None:
                raise MlflowException(
                    "run_not_found", error_code=RESOURCE_DOES_NOT_EXIST
                )
        else:
            payload = self._get(run_id)
        return self._run_entity(payload, self._read_catalog() if self._read_token() else None)

    def _run_entity(
        self, payload: dict[str, Any], catalog: dict[str, Any] | None = None
    ) -> Run:
        metrics: dict[str, Metric] = {}
        for item in payload["metrics"]:
            candidate = Metric(item["key"], item["value"], item["timestamp_ms"], item["step"])
            previous = metrics.get(candidate.key)
            if previous is None or (candidate.step, candidate.timestamp) >= (
                previous.step,
                previous.timestamp,
            ):
                metrics[candidate.key] = candidate
        datasets: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
        models: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
        for artifact in (catalog or {}).get("artifacts", []):
            target = datasets if artifact.get("kind") == "dataset" else models
            if artifact.get("kind") not in {"dataset", "model"}:
                continue
            for version in artifact.get("versions", []):
                target[version["id"]] = (artifact, version)
        dataset_inputs = []
        for version_id in payload.get("input_artifact_version_ids", []):
            value = datasets.get(version_id)
            if value is None:
                continue
            artifact, version = value
            dataset_inputs.append(
                DatasetInput(
                    Dataset(
                        artifact["name"],
                        f"{version['algorithm']}:{version['digest']}",
                        "homebrew-dvc",
                        json.dumps(
                            {
                                "artifact_version_id": version_id,
                                "uri": f"homebrew-dvc://{version_id}",
                            },
                            separators=(",", ":"),
                            sort_keys=True,
                        ),
                    ),
                    [InputTag("homebrew.artifact_version_id", version_id)],
                )
            )
        model_outputs = []
        for version_id in payload.get("output_artifact_version_ids", []):
            value = models.get(version_id)
            if value is not None:
                model_outputs.append(LoggedModelOutput(value[1]["mlflow_model_id"], 0))
        tags = [
            RunTag(item["key"], item["value"])  # type: ignore[no-untyped-call]
            for item in payload["tags"]
        ]
        tag_keys = {item.key for item in tags}
        for key, value in (
            ("mlflow.runName", payload["id"]),
            ("mlflow.user", payload.get("creator_principal_id", "homebrew-mlflow")),
        ):
            if key not in tag_keys:
                tags.append(RunTag(key, value))  # type: ignore[no-untyped-call]
        return Run(
            RunInfo(  # type: ignore[no-untyped-call]
                payload["id"],
                payload["experiment_id"],
                payload.get("creator_principal_id", "homebrew-mlflow"),
                self._status(payload["state"]),
                self._milliseconds(payload["started_at"] or payload.get("created_at")),
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
                tags=tags,
            ),
            RunInputs(dataset_inputs),
            RunOutputs(model_outputs),
        )

    def _experiment_entity(self, payload: dict[str, Any]) -> Experiment:
        return Experiment(  # type: ignore[no-untyped-call]
            payload["id"],
            payload["name"],
            f"homebrew://{payload['id']}",
            "deleted" if payload["archived_at"] else "active",
            tags=[],
            creation_time=self._milliseconds(payload["created_at"]),
            last_update_time=self._milliseconds(payload["last_update_at"]),
            workspace=get_request_workspace(),
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
    ) -> PagedList[Metric]:
        payload = (
            next(
                (item for item in self._read_snapshot()["runs"] if item["id"] == run_id),
                None,
            )
            if self._read_token()
            else self._get(run_id)
        )
        if payload is None:
            raise MlflowException("run_not_found", error_code=RESOURCE_DOES_NOT_EXIST)
        values = [
            Metric(item["key"], item["value"], item["timestamp_ms"], item["step"])
            for item in payload["metrics"]
            if item["key"] == metric_key
        ]
        if max_results is None:
            return PagedList(values, None)
        page, token = SearchUtils.paginate(  # type: ignore[no-untyped-call]
            values, page_token, max_results
        )
        return PagedList(page, token)

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
        if self._read_token():
            payload = next(
                (
                    item
                    for item in self._read_snapshot()["experiments"]
                    if item["id"] == experiment_id
                ),
                None,
            )
            if payload is None:
                raise MlflowException(
                    "experiment_not_found", error_code=RESOURCE_DOES_NOT_EXIST
                )
            return self._experiment_entity(payload)
        run_id = self._bound_run_id()
        if self._get(run_id)["experiment_id"] != experiment_id:
            raise MlflowException("RESOURCE_DOES_NOT_EXIST: experiment is outside this Run")
        return Experiment(  # type: ignore[no-untyped-call]
            experiment_id,
            "Homebrew MLflow Experiment",
            f"homebrew://{run_id}",
            "active",
        )

    def get_experiment_by_name(self, experiment_name: str) -> Experiment | None:
        if not self._read_token():
            return None
        return next(
            (
                self._experiment_entity(item)
                for item in self._read_snapshot()["experiments"]
                if item["name"] == experiment_name
            ),
            None,
        )

    def search_experiments(
        self,
        view_type: int = ViewType.ACTIVE_ONLY,
        max_results: int = 1000,
        filter_string: str | None = None,
        order_by: list[str] | None = None,
        page_token: str | None = None,
    ) -> PagedList[Experiment]:
        experiments = [
            self._experiment_entity(item) for item in self._read_snapshot()["experiments"]
        ]
        if view_type == ViewType.ACTIVE_ONLY:
            experiments = [item for item in experiments if item.lifecycle_stage == "active"]
        elif view_type == ViewType.DELETED_ONLY:
            experiments = [item for item in experiments if item.lifecycle_stage == "deleted"]
        experiments = SearchExperimentsUtils.filter(  # type: ignore[no-untyped-call]
            experiments, filter_string
        )
        experiments = SearchExperimentsUtils.sort(  # type: ignore[no-untyped-call]
            experiments, order_by or ["last_update_time DESC"]
        )
        page, token = SearchExperimentsUtils.paginate(  # type: ignore[no-untyped-call]
            experiments, page_token, max_results
        )
        return PagedList(page, token)

    def _search_runs(
        self,
        experiment_ids: list[str],
        filter_string: str | None,
        run_view_type: int,
        max_results: int,
        order_by: list[str] | None,
        page_token: str | None,
    ) -> tuple[list[Run], str | None]:
        if run_view_type == ViewType.DELETED_ONLY:
            return [], None
        snapshot = self._read_snapshot()
        catalog = self._read_catalog()
        runs = [
            self._run_entity(item, catalog)
            for item in snapshot["runs"]
            if item["experiment_id"] in experiment_ids
        ]
        runs = SearchUtils.filter(runs, filter_string)  # type: ignore[no-untyped-call]
        runs = SearchUtils.sort(runs, order_by)  # type: ignore[no-untyped-call]
        page, token = SearchUtils.paginate(  # type: ignore[no-untyped-call]
            runs, page_token, max_results
        )
        return cast(list[Run], page), cast(str | None, token)

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

    def delete_experiment(self, experiment_id: str) -> None:
        raise _unsupported("delete_experiment")

    def restore_experiment(self, experiment_id: str) -> None:
        raise _unsupported("restore_experiment")

    def rename_experiment(self, experiment_id: str, new_name: str) -> None:
        raise _unsupported("rename_experiment")

    def set_experiment_tag(self, experiment_id: str, tag: Any) -> None:
        raise _unsupported("set_experiment_tag")

    def delete_experiment_tag(self, experiment_id: str, key: str) -> None:
        raise _unsupported("delete_experiment_tag")

    def log_inputs(self, run_id: str, datasets: Any = None, models: Any = None) -> None:
        raise _unsupported("log_inputs")

    def link_traces_to_run(self, trace_ids: list[str], run_id: str) -> None:
        raise _unsupported("link_traces_to_run")

    @staticmethod
    def _logged_model(
        artifact: dict[str, Any], version: dict[str, Any], run: dict[str, Any]
    ) -> LoggedModel:
        timestamp = HomebrewTrackingStore._milliseconds(version["published_at"]) or 0
        latest_metrics: dict[str, Metric] = {}
        for item in run.get("metrics", []):
            metric = Metric(
                item["key"],
                item["value"],
                item["timestamp_ms"],
                item["step"],
                run_id=run["id"],
                model_id=version["mlflow_model_id"],
            )
            previous = latest_metrics.get(metric.key)
            if previous is None or (metric.step, metric.timestamp) >= (
                previous.step,
                previous.timestamp,
            ):
                latest_metrics[metric.key] = metric
        return LoggedModel(
            run["experiment_id"],
            version["mlflow_model_id"],
            f"{artifact['name']}-v{version['sequence']}",
            f"homebrew-dvc://{version['id']}",
            timestamp,
            timestamp,
            model_type="dvc",
            source_run_id=run["id"],
            tags={
                "homebrew.artifact_id": artifact["id"],
                "homebrew.artifact_version_id": version["id"],
                "homebrew.dvc_digest": f"{version['algorithm']}:{version['digest']}",
            },
            params={item["key"]: item["value"] for item in run.get("parameters", [])},
            metrics=list(latest_metrics.values()),
        )

    def _logged_models(self) -> list[LoggedModel]:
        snapshot = self._read_snapshot()
        runs = {item["id"]: item for item in snapshot["runs"]}
        values: list[LoggedModel] = []
        for artifact in self._read_catalog().get("artifacts", []):
            if artifact.get("kind") != "model":
                continue
            for version in artifact.get("versions", []):
                run = runs.get(version.get("producing_run_id"))
                if run is not None:
                    values.append(self._logged_model(artifact, version, run))
        return values

    def search_logged_models(
        self,
        experiment_ids: list[str],
        filter_string: str | None = None,
        datasets: list[dict[str, Any]] | None = None,
        max_results: int | None = None,
        order_by: list[dict[str, Any]] | None = None,
        page_token: str | None = None,
    ) -> PagedList[LoggedModel]:
        values = [
            value for value in self._logged_models() if value.experiment_id in experiment_ids
        ]
        values = SearchLoggedModelsUtils.filter_logged_models(
            values, filter_string, datasets
        )
        values = SearchLoggedModelsUtils.sort(values, order_by)  # type: ignore[no-untyped-call]
        page, token = SearchUtils.paginate(  # type: ignore[no-untyped-call]
            values, page_token, max_results or 1000
        )
        return PagedList(page, token)

    def get_logged_model(self, model_id: str, allow_deleted: bool = False) -> LoggedModel:
        value = next(
            (item for item in self._logged_models() if item.model_id == model_id), None
        )
        if value is None:
            raise MlflowException("logged_model_not_found", error_code=RESOURCE_DOES_NOT_EXIST)
        return value

    def create_logged_model(self, *args: Any, **kwargs: Any) -> Any:
        raise _unsupported("create_logged_model")

    def search_datasets(self, *args: Any, **kwargs: Any) -> Any:
        raise _unsupported("search_datasets")

    def _search_datasets(self, experiment_ids: list[str]) -> list[_DatasetSummary]:
        """Return the DVC dataset versions referenced by Runs in the experiments."""
        snapshot = self._read_snapshot()
        requested = set(experiment_ids)
        run_experiments: dict[str, set[str]] = {}
        for run in snapshot["runs"]:
            if run["experiment_id"] not in requested:
                continue
            for version_id in run.get("input_artifact_version_ids", []):
                run_experiments.setdefault(version_id, set()).add(run["experiment_id"])
        summaries: dict[tuple[str, str, str], _DatasetSummary] = {}
        for artifact in self._read_catalog().get("artifacts", []):
            if artifact.get("kind") != "dataset":
                continue
            for version in artifact.get("versions", []):
                experiment_ids_for_version = run_experiments.get(version["id"])
                if not experiment_ids_for_version:
                    continue
                digest = f"{version['algorithm']}:{version['digest']}"
                for experiment_id in experiment_ids_for_version:
                    key = (experiment_id, artifact["name"], digest)
                    summaries[key] = _DatasetSummary(  # type: ignore[no-untyped-call]
                        experiment_id=experiment_id,
                        name=artifact["name"],
                        digest=digest,
                        context=None,
                    )
        return list(summaries.values())

    def search_traces(self, *args: Any, **kwargs: Any) -> Any:
        raise _unsupported("search_traces")

    def search_mcp_servers(self, *args: Any, **kwargs: Any) -> Any:
        raise _unsupported("search_mcp_servers")

    def list_gateway_endpoints(self, *args: Any, **kwargs: Any) -> Any:
        raise _unsupported("list_gateway_endpoints")

    def list_endpoint_bindings(self, *args: Any, **kwargs: Any) -> Any:
        raise _unsupported("list_endpoint_bindings")
