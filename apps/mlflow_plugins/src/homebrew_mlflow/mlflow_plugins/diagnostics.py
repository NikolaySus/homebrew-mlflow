from __future__ import annotations

import os

from mlflow import MlflowClient
from mlflow.exceptions import MlflowException
from mlflow.tracking.request_auth.registry import fetch_auth
from requests import Request


def main() -> int:
    provider_name = os.environ.get("MLFLOW_TRACKING_AUTH")
    auth = fetch_auth(provider_name)
    prepared = Request("GET", "https://diagnostic.invalid/").prepare()
    if auth is None or not prepared:
        print("mlflow_client_auth=failed")
        return 2
    auth(prepared)
    authorization = prepared.headers.get("Authorization", "")
    if not authorization.startswith("Bearer "):
        print("mlflow_client_auth=failed")
        return 2
    print("mlflow_client_auth=ok")

    try:
        MlflowClient().get_run("diagnostic-invalid-run")
    except MlflowException as error:
        if error.error_code in {"CUSTOMER_UNAUTHORIZED", "UNAUTHENTICATED", "PERMISSION_DENIED"}:
            print("mlflow_auth_boundary=ok")
            return 0
    except Exception:
        pass
    print("mlflow_auth_boundary=failed")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
