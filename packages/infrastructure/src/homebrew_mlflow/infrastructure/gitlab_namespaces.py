from __future__ import annotations

import httpx
from homebrew_mlflow.application import HostedNamespace


class GitLabNamespaceHost:
    def __init__(
        self,
        base_url: str,
        access_token: str,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=30)
        self._headers = {"PRIVATE-TOKEN": access_token}

    def create_private(self, name: str, slug: str) -> HostedNamespace:
        response = self._client.post(
            f"{self._base_url}/api/v4/groups",
            headers=self._headers,
            json={"name": name, "path": slug, "visibility": "private"},
        )
        response.raise_for_status()
        payload = response.json()
        try:
            return HostedNamespace(str(payload["id"]), str(payload["full_path"]))
        except (KeyError, TypeError) as error:
            raise RuntimeError("GitLab returned an invalid group response") from error
