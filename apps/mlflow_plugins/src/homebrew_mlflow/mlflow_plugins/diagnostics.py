from __future__ import annotations

import os

from mlflow.tracking.request_auth.registry import fetch_auth
from requests import Request, Session


def main() -> int:
    provider_name = os.environ.get("MLFLOW_TRACKING_AUTH")
    auth = fetch_auth(provider_name)
    tracking_uri = os.environ.get("MLFLOW_TRACKING_URI", "").rstrip("/")
    prepared = Request(
        "GET",
        f"{tracking_uri}/api/2.0/mlflow/runs/get",
        params={"run_id": "diagnostic-invalid-run"},
    ).prepare()
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
        response = Session().send(prepared, timeout=10)
        if response.status_code in {401, 403}:
            print("mlflow_auth_boundary=ok")
            return 0
    except Exception:
        pass
    print("mlflow_auth_boundary=failed")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
