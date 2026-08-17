from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlsplit, urlunsplit

import httpx


@dataclass(frozen=True, slots=True)
class DeviceAuthorization:
    device_code: str
    user_code: str
    verification_uri: str
    verification_uri_complete: str
    expires_in: int
    interval: int


@dataclass(frozen=True, slots=True)
class GitLabIdentity:
    subject: str
    username: str
    display_name: str


class DevicePollStatus(StrEnum):
    AUTHORIZATION_PENDING = "authorization_pending"
    SLOW_DOWN = "slow_down"
    EXPIRED_TOKEN = "expired_token"
    ACCESS_DENIED = "access_denied"


@dataclass(frozen=True, slots=True)
class DevicePollResult:
    status: DevicePollStatus | None
    identity: GitLabIdentity | None = None


class GitLabOAuthProtocolError(RuntimeError):
    pass


class GitLabDeviceOAuthClient:
    def __init__(
        self,
        base_url: str,
        client_id: str,
        client_secret: str,
        *,
        public_base_url: str | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._public_base_url = public_base_url.rstrip("/") if public_base_url else None
        self._client_id = client_id
        self._client_secret = client_secret
        self._client = client or httpx.Client(timeout=15)

    def start(self) -> DeviceAuthorization:
        response = self._client.post(
            f"{self._base_url}/oauth/authorize_device",
            data={"client_id": self._client_id, "scope": "read_user"},
        )
        response.raise_for_status()
        payload = response.json()
        try:
            return DeviceAuthorization(
                device_code=payload["device_code"],
                user_code=payload["user_code"],
                verification_uri=self._public_url(payload["verification_uri"]),
                verification_uri_complete=self._public_url(
                    payload["verification_uri_complete"]
                ),
                expires_in=int(payload["expires_in"]),
                interval=int(payload["interval"]),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise GitLabOAuthProtocolError(
                "invalid GitLab device authorization response"
            ) from error

    def _public_url(self, provider_url: str) -> str:
        if not self._public_base_url:
            return provider_url
        public = urlsplit(self._public_base_url)
        provider = urlsplit(provider_url)
        return urlunsplit(
            (public.scheme, public.netloc, provider.path, provider.query, provider.fragment)
        )

    def poll(self, device_code: str) -> DevicePollResult:
        response = self._client.post(
            f"{self._base_url}/oauth/token",
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "device_code": device_code,
                "client_id": self._client_id,
            },
        )
        if response.status_code == 400:
            error_code = response.json().get("error")
            try:
                return DevicePollResult(status=DevicePollStatus(error_code))
            except ValueError as error:
                raise GitLabOAuthProtocolError("unknown GitLab device-flow error") from error
        response.raise_for_status()
        access_token = response.json().get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise GitLabOAuthProtocolError("GitLab token response omitted access_token")
        try:
            user_response = self._client.get(
                f"{self._base_url}/api/v4/user",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            user_response.raise_for_status()
            user = user_response.json()
            identity = GitLabIdentity(
                subject=str(user["id"]),
                username=user["username"],
                display_name=user.get("name") or user["username"],
            )
        except (KeyError, TypeError) as error:
            raise GitLabOAuthProtocolError("invalid GitLab user response") from error
        finally:
            self._client.post(
                f"{self._base_url}/oauth/revoke",
                data={
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "token": access_token,
                },
            ).raise_for_status()
        return DevicePollResult(status=None, identity=identity)
