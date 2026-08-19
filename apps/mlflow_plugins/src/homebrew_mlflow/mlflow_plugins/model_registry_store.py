from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mlflow.exceptions import MlflowException
from mlflow.protos.databricks_pb2 import INVALID_PARAMETER_VALUE


class UnsupportedModelRegistryStore:
    """Explicitly disabled model registry for the Homebrew compatibility boundary."""

    supports_workspaces = True

    def __init__(self, store_uri: str, tracking_uri: str | None = None) -> None:
        self.store_uri = store_uri

    def __getattr__(self, operation: str) -> Callable[..., Any]:
        def unsupported(*_args: Any, **_kwargs: Any) -> Any:
            raise MlflowException(
                f"unsupported_operation: model registry operation {operation} is disabled",
                error_code=INVALID_PARAMETER_VALUE,
            )

        return unsupported


def build_model_registry_store(
    store_uri: str, tracking_uri: str | None = None
) -> UnsupportedModelRegistryStore:
    return UnsupportedModelRegistryStore(store_uri, tracking_uri)
