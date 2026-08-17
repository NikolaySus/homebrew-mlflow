import base64
import hashlib
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient
from homebrew_mlflow.api.main import create_app
from homebrew_mlflow.infrastructure import Base
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool


class OAuthResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return self._payload


def test_web_oauth_uses_pkce_one_time_context_and_csrf(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    token_requests: list[dict[str, object]] = []
    revoked: list[str] = []

    def post(url: str, *, data, timeout: int):  # type: ignore[no-untyped-def]
        assert timeout == 20
        if url.endswith("/oauth/token"):
            token_requests.append(dict(data))
            return OAuthResponse({"access_token": "short-lived-gitlab-token"})
        assert url.endswith("/oauth/revoke")
        revoked.append(str(data["token"]))
        return OAuthResponse({})

    def get(url: str, *, headers, timeout: int):  # type: ignore[no-untyped-def]
        assert url.endswith("/api/v4/user")
        assert headers == {"Authorization": "Bearer short-lived-gitlab-token"}
        assert timeout == 20
        return OAuthResponse({"id": 17, "username": "researcher", "name": "Researcher"})

    monkeypatch.setattr("homebrew_mlflow.api.auth.create_session", lambda _url: Session(engine))
    monkeypatch.setattr("homebrew_mlflow.api.auth.httpx.post", post)
    monkeypatch.setattr("homebrew_mlflow.api.auth.httpx.get", get)
    client = TestClient(create_app())

    started = client.get("/api/v1/auth/web/start", follow_redirects=False)
    assert started.status_code == 307
    query = parse_qs(urlparse(started.headers["location"]).query)
    assert urlparse(started.headers["location"]).netloc == "git.localhost:8080"
    assert query["code_challenge_method"] == ["S256"]
    assert "code_verifier" not in query
    assert "client_secret" not in query

    completed = client.get(
        "/api/v1/auth/web/callback",
        params={"code": "one-time-code", "state": query["state"][0]},
        follow_redirects=False,
    )
    assert completed.status_code == 303
    verifier = str(token_requests[0]["code_verifier"])
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode()
    assert challenge.rstrip("=") == query["code_challenge"][0]
    assert revoked == ["short-lived-gitlab-token"]

    assert client.post("/api/v1/auth/web/session").status_code == 403
    csrf = client.cookies["hm_csrf"]
    session = client.post("/api/v1/auth/web/session", headers={"X-CSRF-Token": csrf})
    assert session.status_code == 200
    assert session.json()["token_type"] == "Bearer"
