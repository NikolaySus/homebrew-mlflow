from fastapi.testclient import TestClient
from homebrew_mlflow.api.main import create_app
from homebrew_mlflow.infrastructure import DevicePollResult, DevicePollStatus


class PendingGitLabClient:
    def poll(self, _device_code: str) -> DevicePollResult:
        return DevicePollResult(DevicePollStatus.AUTHORIZATION_PENDING)


def test_device_poll_pending_response_is_safe_and_contract_shaped(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr("homebrew_mlflow.api.auth._gitlab_client", lambda: PendingGitLabClient())

    response = TestClient(create_app()).post(
        "/api/v1/auth/device/poll", json={"device_code": "opaque-device-code"}
    )

    assert response.status_code == 200
    assert response.json() == {"status": "authorization_pending"}
    assert response.headers["X-Request-ID"].startswith("req_")
