from homebrew_mlflow.api.observability import RequestRateLimiter
from starlette.requests import Request


def _request(path: str, forwarded_for: str = "203.0.113.10") -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "https",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": [(b"x-forwarded-for", forwarded_for.encode())],
            "client": ("gateway", 8080),
            "server": ("api", 8000),
        }
    )


def test_mlflow_gateway_authorization_allows_native_ui_request_burst() -> None:
    limiter = RequestRateLimiter()
    path = "/api/v1/auth/mlflow/authorize"

    assert all(limiter.permit(_request(path)) for _ in range(1200))
    assert not limiter.permit(_request(path))


def test_rate_limit_uses_original_forwarded_client() -> None:
    limiter = RequestRateLimiter()
    path = "/api/v1/auth/web/session"

    assert all(limiter.permit(_request(path)) for _ in range(60))
    assert not limiter.permit(_request(path))
    assert limiter.permit(_request(path, "198.51.100.25"))
