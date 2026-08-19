from __future__ import annotations

import os
from typing import Any

import requests
from mlflow.entities import Workspace
from mlflow.exceptions import MlflowException
from mlflow.protos.databricks_pb2 import RESOURCE_DOES_NOT_EXIST
from mlflow.store.workspace.abstract_store import AbstractStore

from .auth_context import (
    authorization_header,
    project_for_workspace,
    token_claims,
    workspace_for_project,
)


class HomebrewWorkspaceStore(AbstractStore):
    def __init__(self, workspace_uri: str) -> None:
        self._base_url = os.environ["HOMEBREW_MLFLOW_PLATFORM_INTERNAL_URL"].rstrip("/")

    def _values(self) -> list[dict[str, Any]]:
        response = requests.get(
            f"{self._base_url}/api/v1/mlflow/workspaces",
            headers=authorization_header(),
            timeout=30,
        )
        if not getattr(response, "ok", True):
            raise MlflowException(
                f"platform_request_failed: status={response.status_code}"
            )
        return list(response.json())

    @staticmethod
    def _entity(value: dict[str, Any]) -> Workspace:
        return Workspace(
            name=value["name"],
            description=f"{value['project_name']} ({value['project_slug']})",
            default_artifact_root=f"homebrew://{value['project_id']}",
        )

    def list_workspaces(self) -> list[Workspace]:
        return [self._entity(value) for value in self._values()]

    def get_workspace(self, workspace_name: str) -> Workspace:
        try:
            project_id = project_for_workspace(workspace_name)
        except MlflowException as error:
            raise MlflowException(
                f"Workspace '{workspace_name}' not found",
                error_code=RESOURCE_DOES_NOT_EXIST,
            ) from error
        return Workspace(
            name=workspace_for_project(project_id),
            description=f"Homebrew MLflow project {project_id}",
            default_artifact_root=f"homebrew://{project_id}",
        )

    def get_default_workspace(self) -> Workspace:
        project_id = token_claims().get("prj")
        if not isinstance(project_id, str):
            raise MlflowException.invalid_parameter_value("active workspace is required")
        return self.get_workspace(workspace_for_project(project_id))

    def create_workspace(self, workspace: Workspace) -> Workspace:
        raise MlflowException("unsupported_operation: create_workspace")

    def update_workspace(self, workspace: Workspace) -> Workspace:
        raise MlflowException("unsupported_operation: update_workspace")

    def delete_workspace(self, workspace_name: str, mode: Any = None) -> None:
        raise MlflowException("unsupported_operation: delete_workspace")

    def resolve_artifact_root(
        self, default_artifact_root: str | None, workspace_name: str
    ) -> tuple[str | None, bool]:
        workspace = self.get_workspace(workspace_name)
        return workspace.default_artifact_root, False
