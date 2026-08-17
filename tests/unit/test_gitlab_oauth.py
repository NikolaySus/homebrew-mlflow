import httpx
import pytest
from homebrew_mlflow.infrastructure import (
    DevicePollStatus,
    GitLabDeviceOAuthClient,
    GitLabOAuthProtocolError,
)


def test_device_start_uses_gitlab_protocol() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/oauth/authorize_device"
        assert b"scope=read_user" in request.content
        return httpx.Response(
            200,
            json={
                "device_code": "secret-device-code",
                "user_code": "ABCD1234",
                "verification_uri": "https://gitlab.example/oauth/device",
                "verification_uri_complete": "https://gitlab.example/oauth/device?user_code=ABCD1234",
                "expires_in": 300,
                "interval": 5,
            },
        )

    client = GitLabDeviceOAuthClient(
        "https://gitlab.example",
        "client-id",
        "client-secret",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    authorization = client.start()
    assert authorization.user_code == "ABCD1234"
    assert authorization.interval == 5


def test_device_start_rewrites_internal_verification_urls() -> None:
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200,
            json={
                "device_code": "secret-device-code",
                "user_code": "ABCD1234",
                "verification_uri": "https://gitlab/oauth/device",
                "verification_uri_complete": (
                    "https://gitlab/oauth/device?user_code=ABCD1234"
                ),
                "expires_in": 300,
                "interval": 5,
            },
        )
    )
    client = GitLabDeviceOAuthClient(
        "http://gitlab",
        "client-id",
        "client-secret",
        public_base_url="https://git.ml.spkya.ru",
        client=httpx.Client(transport=transport),
    )

    authorization = client.start()

    assert authorization.verification_uri == "https://git.ml.spkya.ru/oauth/device"
    assert authorization.verification_uri_complete == (
        "https://git.ml.spkya.ru/oauth/device?user_code=ABCD1234"
    )


def test_pending_poll_returns_stable_status() -> None:
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(400, json={"error": "authorization_pending"})
    )
    client = GitLabDeviceOAuthClient(
        "https://gitlab.example",
        "client-id",
        "client-secret",
        client=httpx.Client(transport=transport),
    )
    assert client.poll("device-code").status is DevicePollStatus.AUTHORIZATION_PENDING


def test_success_reads_identity_and_revokes_gitlab_token() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/oauth/token":
            return httpx.Response(200, json={"access_token": "temporary-gitlab-token"})
        if request.url.path == "/api/v4/user":
            assert request.headers["Authorization"] == "Bearer temporary-gitlab-token"
            return httpx.Response(
                200,
                json={
                    "id": 42,
                    "username": "ada",
                    "email": "ada@example.com",
                    "name": "Ada",
                },
            )
        if request.url.path == "/oauth/revoke":
            assert b"temporary-gitlab-token" in request.content
            return httpx.Response(200, json={})
        raise AssertionError(request.url)

    client = GitLabDeviceOAuthClient(
        "https://gitlab.example",
        "client-id",
        "client-secret",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    result = client.poll("device-code")
    assert result.identity is not None
    assert result.identity.subject == "42"
    assert result.identity.email == "ada@example.com"
    assert calls == ["/oauth/token", "/api/v4/user", "/oauth/revoke"]


def test_unknown_gitlab_poll_error_is_not_leaked() -> None:
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(400, json={"error": "unexpected_provider_detail"})
    )
    client = GitLabDeviceOAuthClient(
        "https://gitlab.example",
        "client-id",
        "client-secret",
        client=httpx.Client(transport=transport),
    )
    with pytest.raises(GitLabOAuthProtocolError, match="unknown"):
        client.poll("device-code")
