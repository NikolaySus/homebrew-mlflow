from __future__ import annotations

import os
import time
from pathlib import Path

from mlflow.tracking.request_auth.abstract_request_auth_provider import RequestAuthProvider
from requests import PreparedRequest


class _TokenFileAuth:
    """Requests auth hook that reloads the rotating Run token for every request."""

    def __call__(self, request: PreparedRequest) -> PreparedRequest:
        if "Authorization" in request.headers:
            return request
        token_file = os.environ.get("MLFLOW_TRACKING_TOKEN_FILE")
        if not token_file:
            raise RuntimeError("MLFLOW_TRACKING_TOKEN_FILE is not configured")
        token = ""
        error: OSError | None = None
        for delay in (0.0, 0.01, 0.05):
            if delay:
                time.sleep(delay)
            try:
                token = Path(token_file).read_text(encoding="utf-8").strip()
                error = None
            except OSError as caught:
                error = caught
            if token and not any(character.isspace() for character in token):
                break
            token = ""
        if not token:
            message = "Run-scoped MLflow token file is unavailable or empty"
            raise RuntimeError(message) from error
        request.headers["Authorization"] = f"Bearer {token}"
        return request


class HomebrewTokenFileAuthProvider(RequestAuthProvider):
    def get_name(self) -> str:
        return "homebrew-token-file"

    def get_auth(self) -> _TokenFileAuth:
        return _TokenFileAuth()
