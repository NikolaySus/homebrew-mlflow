from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any

from flask import has_request_context, request
from mlflow.exceptions import MlflowException
from mlflow.protos.databricks_pb2 import CUSTOMER_UNAUTHORIZED


def authorization_header() -> dict[str, str]:
    authorization = request.headers.get("Authorization") if has_request_context() else None
    if authorization is None:
        token_file = os.environ.get("MLFLOW_TRACKING_TOKEN_FILE")
        token = (
            Path(token_file).read_text(encoding="utf-8").strip()
            if token_file
            else os.environ.get("MLFLOW_TRACKING_TOKEN")
        )
        authorization = f"Bearer {token}" if token else None
    if not authorization or not authorization.startswith("Bearer "):
        raise MlflowException(
            "authentication_required: missing scoped MLflow token",
            error_code=CUSTOMER_UNAUTHORIZED,
        )
    token = authorization.removeprefix("Bearer ")
    if len(token.split(".")) != 3:
        raise MlflowException(
            "authentication_required: malformed scoped MLflow token",
            error_code=CUSTOMER_UNAUTHORIZED,
        )
    return {"Authorization": authorization}


def token_claims() -> dict[str, Any]:
    try:
        token = authorization_header()["Authorization"].removeprefix("Bearer ")
        payload = token.split(".")[1]
        value = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        if not isinstance(value, dict):
            raise TypeError
        return value
    except (IndexError, TypeError, ValueError, UnicodeError, json.JSONDecodeError) as error:
        raise MlflowException(
            "authentication_required: malformed scoped MLflow token",
            error_code=CUSTOMER_UNAUTHORIZED,
        ) from error


def workspace_for_project(project_id: str) -> str:
    return project_id.replace("pr_", "pr-", 1).lower()


def project_for_workspace(workspace: str) -> str:
    normalized = workspace.strip().lower()
    if not normalized.startswith("pr-"):
        raise MlflowException.invalid_parameter_value("workspace_not_found")
    return "pr_" + normalized.removeprefix("pr-").upper()
