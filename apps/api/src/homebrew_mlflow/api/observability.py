from __future__ import annotations

import json
import logging
import threading
import time
from collections import Counter, defaultdict, deque

from fastapi import Request

logger = logging.getLogger("homebrew_mlflow.api")
request_counts: Counter[tuple[str, str, int]] = Counter()
request_durations: Counter[tuple[str, str]] = Counter()
_lock = threading.Lock()


class RequestRateLimiter:
    def __init__(self) -> None:
        self._requests: defaultdict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def permit(self, request: Request) -> bool:
        path = request.url.path
        limit = self._limit(path)
        if limit is None:
            return True
        key = (self._client_address(request), path)
        now = time.monotonic()
        with self._lock:
            values = self._requests[key]
            while values and values[0] <= now - 60:
                values.popleft()
            if len(values) >= limit:
                return False
            values.append(now)
        return True

    @staticmethod
    def _limit(path: str) -> int | None:
        if path == "/api/v1/auth/mlflow/authorize":
            # Caddy calls this once for every MLflow document, API request, and
            # code-split UI asset. A native MLflow page can legitimately exceed
            # the interactive-login limit during one navigation.
            return 1200
        if path.startswith("/api/v1/auth/"):
            return 60
        if path.endswith("/dvc-credentials"):
            return 120
        if path.endswith("/publication-operations"):
            return 30
        return None

    @staticmethod
    def _client_address(request: Request) -> str:
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",", 1)[0].strip()
        return request.client.host if request.client else "unknown"


def record_request(method: str, path: str, status: int, elapsed_seconds: float) -> None:
    route = _route_label(path)
    with _lock:
        request_counts[(method, route, status)] += 1
        request_durations[(method, route)] += int(elapsed_seconds * 1_000_000)


def log_request(request_id: str, method: str, path: str, status: int, elapsed: float) -> None:
    logger.info(
        json.dumps(
            {
                "event": "http.request",
                "request_id": request_id,
                "method": method,
                "path": _route_label(path),
                "status": status,
                "duration_ms": round(elapsed * 1000, 2),
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )


def prometheus_metrics() -> str:
    lines = [
        "# HELP homebrew_mlflow_http_requests_total Platform HTTP requests.",
        "# TYPE homebrew_mlflow_http_requests_total counter",
    ]
    with _lock:
        for (method, route, status), value in sorted(request_counts.items()):
            lines.append(
                "homebrew_mlflow_http_requests_total"
                f'{{method="{method}",route="{route}",status="{status}"}} {value}'
            )
        lines.extend(
            (
                "# HELP homebrew_mlflow_http_request_duration_microseconds_total "
                "Cumulative request duration.",
                "# TYPE homebrew_mlflow_http_request_duration_microseconds_total counter",
            )
        )
        for (method, route), value in sorted(request_durations.items()):
            lines.append(
                "homebrew_mlflow_http_request_duration_microseconds_total"
                f'{{method="{method}",route="{route}"}} {value}'
            )
    return "\n".join(lines) + "\n"


def _route_label(path: str) -> str:
    parts = path.split("/")
    return "/".join("{id}" if "_" in part and len(part) > 20 else part for part in parts)
