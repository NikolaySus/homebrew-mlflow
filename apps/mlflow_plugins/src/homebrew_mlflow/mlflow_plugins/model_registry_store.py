from __future__ import annotations

import os
from collections.abc import Callable
from datetime import datetime
from typing import Any, cast

import requests
from mlflow.entities.model_registry import (
    ModelVersion,
    ModelVersionTag,
    RegisteredModel,
    RegisteredModelAlias,
    RegisteredModelTag,
)
from mlflow.exceptions import MlflowException
from mlflow.protos.databricks_pb2 import (
    INVALID_PARAMETER_VALUE,
    RESOURCE_DOES_NOT_EXIST,
)
from mlflow.store.entities.paged_list import PagedList
from mlflow.utils.search_utils import SearchModelUtils, SearchModelVersionUtils
from mlflow.utils.workspace_context import get_request_workspace

from .auth_context import authorization_header, token_claims


class HomebrewModelRegistryStore:
    """Read-only MLflow view over canonical DVC model Artifact Versions."""

    supports_workspaces = True

    def __init__(self, store_uri: str, tracking_uri: str | None = None) -> None:
        self.store_uri = store_uri
        self._base_url = os.environ["HOMEBREW_MLFLOW_PLATFORM_INTERNAL_URL"].rstrip("/")

    def _catalog(self) -> list[dict[str, Any]]:
        workspace = get_request_workspace()
        if not workspace:
            project_id = token_claims().get("prj")
            if not isinstance(project_id, str):
                raise MlflowException.invalid_parameter_value("active workspace is required")
            workspace = project_id.replace("pr_", "pr-", 1).lower()
        response = requests.get(
            f"{self._base_url}/api/v1/mlflow/workspaces/{workspace}/catalog",
            headers=authorization_header(),
            timeout=30,
        )
        if not response.ok:
            raise MlflowException(f"platform_request_failed: status={response.status_code}")
        payload = cast(dict[str, Any], response.json())
        return [item for item in payload["artifacts"] if item["kind"] == "model"]

    @staticmethod
    def _milliseconds(value: str) -> int:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)

    @classmethod
    def _model_version(
        cls, artifact: dict[str, Any], version: dict[str, Any]
    ) -> ModelVersion:
        aliases = [
            value["alias"]
            for value in artifact.get("aliases", [])
            if value["artifact_version_id"] == version["id"]
        ]
        timestamp = cls._milliseconds(version["published_at"])
        workspace = get_request_workspace()
        return ModelVersion(
            artifact["name"],
            str(version["sequence"]),
            timestamp,
            timestamp,
            description=artifact.get("description"),
            current_stage="None",
            source=f"homebrew-dvc://{version['id']}",
            run_id=version.get("producing_run_id"),
            status="READY",
            tags=[
                ModelVersionTag(  # type: ignore[no-untyped-call]
                    "homebrew.artifact_version_id", version["id"]
                ),
                ModelVersionTag(  # type: ignore[no-untyped-call]
                    "homebrew.dvc_digest",
                    f"{version['algorithm']}:{version['digest']}",
                ),
            ],
            aliases=aliases,
            model_id=version["mlflow_model_id"],
            workspace=workspace,
        )

    @classmethod
    def _registered_model(cls, artifact: dict[str, Any]) -> RegisteredModel:
        versions = [cls._model_version(artifact, value) for value in artifact["versions"]]
        timestamps = [value.creation_timestamp for value in versions]
        created = min(timestamps) if timestamps else cls._milliseconds(artifact["created_at"])
        updated = max(timestamps) if timestamps else created
        latest = [max(versions, key=lambda value: int(value.version))] if versions else []
        return RegisteredModel(
            artifact["name"],
            created,
            updated,
            description=artifact.get("description"),
            latest_versions=latest,
            tags=[
                RegisteredModelTag(  # type: ignore[no-untyped-call]
                    "homebrew.artifact_id", artifact["id"]
                )
            ],
            aliases=[
                RegisteredModelAlias(  # type: ignore[no-untyped-call]
                    value["alias"],
                    str(
                        next(
                            version["sequence"]
                            for version in artifact["versions"]
                            if version["id"] == value["artifact_version_id"]
                        )
                    ),
                )
                for value in artifact.get("aliases", [])
            ],
            workspace=get_request_workspace(),
        )

    def search_registered_models(
        self,
        filter_string: str | None = None,
        max_results: int | None = None,
        order_by: list[str] | None = None,
        page_token: str | None = None,
    ) -> PagedList[RegisteredModel]:
        values = [self._registered_model(item) for item in self._catalog()]
        values = SearchModelUtils.filter(values, filter_string)  # type: ignore[no-untyped-call]
        values = SearchModelUtils.sort(values, order_by)  # type: ignore[no-untyped-call]
        page, token = SearchModelUtils.paginate(  # type: ignore[no-untyped-call]
            values, page_token, max_results or 1000
        )
        return PagedList(page, token)

    def get_registered_model(self, name: str) -> RegisteredModel:
        value = next((item for item in self._catalog() if item["name"] == name), None)
        if value is None:
            raise MlflowException("registered_model_not_found", RESOURCE_DOES_NOT_EXIST)
        return self._registered_model(value)

    def search_model_versions(
        self,
        filter_string: str | None = None,
        max_results: int | None = None,
        order_by: list[str] | None = None,
        page_token: str | None = None,
    ) -> PagedList[ModelVersion]:
        values = [
            self._model_version(artifact, version)
            for artifact in self._catalog()
            for version in artifact["versions"]
        ]
        values = SearchModelVersionUtils.filter(  # type: ignore[no-untyped-call]
            values, filter_string
        )
        values = SearchModelVersionUtils.sort(  # type: ignore[no-untyped-call]
            values, order_by
        )
        page, token = SearchModelVersionUtils.paginate(  # type: ignore[no-untyped-call]
            values, page_token, max_results or 1000
        )
        return PagedList(page, token)

    def get_model_version(self, name: str, version: str) -> ModelVersion:
        artifact = next((item for item in self._catalog() if item["name"] == name), None)
        value = (
            next(
                (
                    item
                    for item in artifact["versions"]
                    if str(item["sequence"]) == str(version)
                ),
                None,
            )
            if artifact is not None
            else None
        )
        if artifact is None or value is None:
            raise MlflowException("model_version_not_found", RESOURCE_DOES_NOT_EXIST)
        return self._model_version(artifact, value)

    def get_latest_versions(
        self, name: str, stages: list[str] | None = None
    ) -> list[ModelVersion]:
        model = self.get_registered_model(name)
        if stages and "None" not in stages:
            return []
        return list(model.latest_versions)

    def get_model_version_by_alias(self, name: str, alias: str) -> ModelVersion:
        artifact = next((item for item in self._catalog() if item["name"] == name), None)
        target = (
            next(
                (
                    value["artifact_version_id"]
                    for value in artifact.get("aliases", [])
                    if value["alias"] == alias
                ),
                None,
            )
            if artifact is not None
            else None
        )
        version = (
            next((item for item in artifact["versions"] if item["id"] == target), None)
            if artifact is not None and target is not None
            else None
        )
        if artifact is None or version is None:
            raise MlflowException("model_alias_not_found", RESOURCE_DOES_NOT_EXIST)
        return self._model_version(artifact, version)

    def get_model_version_download_uri(self, name: str, version: str) -> str:
        value = self.get_model_version(name, version)
        return str(value.source)

    def __getattr__(self, operation: str) -> Callable[..., Any]:
        def unsupported(*_args: Any, **_kwargs: Any) -> Any:
            raise MlflowException(
                f"unsupported_operation: model registry operation {operation} is disabled",
                error_code=INVALID_PARAMETER_VALUE,
            )

        return unsupported


def build_model_registry_store(
    store_uri: str, tracking_uri: str | None = None
) -> HomebrewModelRegistryStore:
    return HomebrewModelRegistryStore(store_uri, tracking_uri)
