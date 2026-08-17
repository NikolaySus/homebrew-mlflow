"""Exercise the public Compose boundaries after a full-stack deployment."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import parse_qs, urlparse

PUBLIC_BASE_URL = os.getenv(
    "HOMEBREW_MLFLOW_ACCEPTANCE_PUBLIC_BASE_URL", "http://localhost:8080"
).rstrip("/")
GITLAB_BASE_URL = os.getenv(
    "HOMEBREW_MLFLOW_ACCEPTANCE_GITLAB_BASE_URL", "http://git.localhost:8080"
).rstrip("/")
INFISICAL_BASE_URL = os.getenv(
    "HOMEBREW_MLFLOW_ACCEPTANCE_INFISICAL_BASE_URL", "http://secrets.localhost:8080"
).rstrip("/")
GRAFANA_BASE_URL = os.getenv(
    "HOMEBREW_MLFLOW_ACCEPTANCE_GRAFANA_BASE_URL", "http://ops.localhost:8080"
).rstrip("/")
COMPOSE_FILES = tuple(
    Path(value)
    for value in os.getenv(
        "HOMEBREW_MLFLOW_ACCEPTANCE_COMPOSE_FILES", "deploy/compose/compose.yaml"
    ).split(",")
)
COMPOSE_ENV_FILE = os.getenv("HOMEBREW_MLFLOW_ACCEPTANCE_COMPOSE_ENV_FILE")
ENDPOINTS = {
    "platform_live": f"{PUBLIC_BASE_URL}/health/live",
    "platform_ready": f"{PUBLIC_BASE_URL}/health/ready",
    "openapi": f"{PUBLIC_BASE_URL}/openapi.json",
    "gitlab": f"{GITLAB_BASE_URL}/users/sign_in",
    "infisical": f"{INFISICAL_BASE_URL}/api/status",
    "grafana": f"{GRAFANA_BASE_URL}/api/health",
}


def get(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=10) as response:  # noqa: S310
        if response.status != 200:
            raise RuntimeError(f"{url} returned HTTP {response.status}")
        return bytes(response.read())


def wait_for(name: str, url: str, deadline: float) -> bytes:
    last_error = "not attempted"
    while time.monotonic() < deadline:
        try:
            return get(url)
        except (OSError, RuntimeError, urllib.error.URLError) as error:
            last_error = str(error)
            time.sleep(2)
    raise RuntimeError(f"{name} did not become ready: {last_error}")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(  # type: ignore[no-untyped-def]
        self, request, file_pointer, code, message, headers, new_url
    ):
        return None


def assert_bootstrapped_oauth() -> None:
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        opener.open(  # noqa: S310
            f"{PUBLIC_BASE_URL}/api/v1/auth/web/start", timeout=10
        )
    except urllib.error.HTTPError as response:
        if response.code not in {302, 303, 307}:
            raise RuntimeError(f"OAuth start returned HTTP {response.code}") from response
        location = response.headers.get("Location", "")
        parsed = urlparse(location)
        client_id = parse_qs(parsed.query).get("client_id", [""])[0]
        if parsed.netloc != urlparse(GITLAB_BASE_URL).netloc:
            raise RuntimeError(
                "OAuth start did not redirect to the Compose GitLab"
            ) from response
        if not client_id or client_id == "development-client":
            raise RuntimeError("OAuth start still uses the placeholder client") from response
        return
    raise RuntimeError("OAuth start did not redirect")


def assert_bootstrapped_device_oauth() -> None:
    request = urllib.request.Request(
        f"{PUBLIC_BASE_URL}/api/v1/auth/device/start", method="POST"
    )
    with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
        if response.status != 200:
            raise RuntimeError(f"device OAuth start returned HTTP {response.status}")
        payload = json.loads(response.read())
    verification_uri = urlparse(str(payload.get("verification_uri", "")))
    if verification_uri.netloc != urlparse(GITLAB_BASE_URL).netloc:
        raise RuntimeError("device OAuth start did not use the Compose GitLab")
    if not payload.get("device_code") or not payload.get("user_code"):
        raise RuntimeError("device OAuth start omitted required codes")
    poll_request = urllib.request.Request(
        f"{PUBLIC_BASE_URL}/api/v1/auth/device/poll",
        data=json.dumps({"device_code": payload["device_code"]}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(poll_request, timeout=10) as response:  # noqa: S310
        if response.status != 200:
            raise RuntimeError(f"device OAuth poll returned HTTP {response.status}")
        poll_payload = json.loads(response.read())
    if poll_payload.get("status") != "authorization_pending":
        raise RuntimeError("device OAuth poll did not report authorization_pending")


def compose(*arguments: str, capture: bool = False) -> subprocess.CompletedProcess[str]:
    command = ["docker", "compose"]
    if COMPOSE_ENV_FILE:
        command.extend(["--env-file", COMPOSE_ENV_FILE])
    for compose_file in COMPOSE_FILES:
        command.extend(["-f", str(compose_file)])
    return subprocess.run(
        [*command, *arguments],
        check=True,
        text=True,
        capture_output=capture,
    )


def main() -> int:
    deadline = time.monotonic() + 300
    responses = {
        name: wait_for(name, url, deadline) for name, url in ENDPOINTS.items()
    }
    contract = json.loads(responses["openapi"])
    required_paths = {
        "/api/v1/setup/status",
        "/api/v1/projects/{project_id}/repositories/{repository_id}/retry-provisioning",
        "/api/v1/projects/{project_id}/runs",
        "/api/v1/projects/{project_id}/environment-specifications",
        "/api/v1/projects/{project_id}/environment-specifications/resolve",
        "/api/v1/projects/{project_id}/pipeline-versions/resolve",
        "/api/v1/projects/{project_id}/publication-operations",
        "/api/v1/projects/{project_id}/shared-artifact-references",
        "/api/v1/projects/{project_id}/memberships/{principal_id}/recover-maintainer",
    }
    missing = required_paths - set(contract["paths"])
    if missing:
        raise RuntimeError(f"OpenAPI is missing required paths: {sorted(missing)}")
    if b"gitlab" not in responses["gitlab"].lower():
        raise RuntimeError("GitLab sign-in page was not recognizable")
    assert_bootstrapped_oauth()
    assert_bootstrapped_device_oauth()
    compose(
        "exec",
        "-T",
        "gitlab",
        "env",
        f"HOMEBREW_MLFLOW_GITLAB_HOST={urlparse(GITLAB_BASE_URL).hostname}",
        "/bin/sh",
        "/bootstrap/validate-gitlab.sh",
    )
    compose(
        "exec",
        "-T",
        "gitlab",
        "/bin/sh",
        "-c",
        "test -s /run/platform-secrets/infisical-token",
    )
    migration = compose(
        "exec",
        "-T",
        "postgres",
        "psql",
        "-U",
        "homebrew_mlflow",
        "-d",
        "homebrew_mlflow",
        "-tAc",
        "SELECT version_num FROM alembic_version",
        capture=True,
    ).stdout.strip()
    if migration != "0022_machine_credential_expiry":
        raise RuntimeError(f"unexpected migration head: {migration}")
    running = set(compose("ps", "--status", "running", "--services", capture=True).stdout.split())
    required_services = {"api", "publication-worker-1", "publication-worker-2"}
    if missing_services := required_services - running:
        raise RuntimeError(f"required services are not running: {sorted(missing_services)}")
    compose(
        "exec",
        "-T",
        "prometheus",
        "promtool",
        "check",
        "config",
        "/etc/prometheus/prometheus.yml",
    )
    print("Compose acceptance boundaries passed: " + ", ".join(ENDPOINTS))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, subprocess.CalledProcessError) as error:
        print(f"Compose acceptance failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
