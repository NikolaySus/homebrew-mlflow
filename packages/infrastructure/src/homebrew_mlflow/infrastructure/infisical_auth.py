from __future__ import annotations

from collections.abc import Callable

InfisicalAccessToken = str | Callable[[], str]


def infisical_authorization_headers(source: InfisicalAccessToken) -> dict[str, str]:
    token = source() if callable(source) else source
    if not token:
        raise RuntimeError("Infisical access token is empty")
    return {"Authorization": f"Bearer {token}"}
