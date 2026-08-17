from __future__ import annotations

import json
import os
import platform
import secrets
import shutil
import subprocess
import threading
import time
import webbrowser
from pathlib import Path
from typing import Annotated, cast

import httpx
import keyring
import typer
from packaging.specifiers import SpecifierSet
from packaging.version import Version

from . import __version__

app = typer.Typer(no_args_is_help=True, pretty_exceptions_show_locals=False)
credentials_app = typer.Typer(no_args_is_help=True, hidden=True)
app.add_typer(credentials_app, name="credentials")
publication_app = typer.Typer(no_args_is_help=True)
app.add_typer(publication_app, name="publication")
artifact_app = typer.Typer(no_args_is_help=True)
app.add_typer(artifact_app, name="artifact")


def _config_path() -> Path:
    return Path(typer.get_app_dir("homebrew-mlflow")) / "config.json"


def _store_refresh(server: str, token: str) -> None:
    keyring.set_password("homebrew-mlflow", server, token)


def _configured_server() -> str | None:
    if not _config_path().exists():
        return None
    value = json.loads(_config_path().read_text(encoding="utf-8")).get("server")
    return value if isinstance(value, str) else None


def _repository_context(start: Path | None = None) -> dict[str, str]:
    current = (start or Path.cwd()).resolve()
    for directory in (current, *current.parents):
        candidate = directory / ".homebrew-mlflow.json"
        if candidate.is_file():
            payload = json.loads(candidate.read_text(encoding="utf-8"))
            required = ("server", "project_id", "repository_id")
            if not all(isinstance(payload.get(key), str) for key in required):
                raise RuntimeError("invalid .homebrew-mlflow.json repository context")
            return {key: payload[key] for key in required}
    raise RuntimeError("not inside a Homebrew MLflow research repository")


def _refresh_session(client: httpx.Client, server: str) -> dict[str, object]:
    current = keyring.get_password("homebrew-mlflow", server)
    if current is None:
        raise RuntimeError("no platform session; run homebrew-mlflow login")
    response = client.post(f"{server}/api/v1/auth/refresh", json={"refresh_token": current})
    response.raise_for_status()
    session = cast(dict[str, object], response.json())
    replacement = session.get("refresh_token")
    access = session.get("access_token")
    if not isinstance(replacement, str) or not isinstance(access, str):
        raise RuntimeError("platform returned an invalid refresh response")
    _store_refresh(server, replacement)
    return session


@app.command()
def version() -> None:
    """Print the helper version."""
    typer.echo(__version__)


@app.command()
def login(
    server: Annotated[str, typer.Option("--server", help="Homebrew MLflow base URL")],
    no_browser: Annotated[bool, typer.Option("--no-browser")] = False,
) -> None:
    """Authenticate through GitLab's terminal device flow."""
    normalized = server.rstrip("/")
    local = normalized.startswith("http://localhost:") or normalized.startswith("http://127.0.0.1:")
    if not normalized.startswith("https://") and not local:
        raise typer.BadParameter("the server must use HTTPS outside localhost")
    with httpx.Client(timeout=15) as client:
        started_response = client.post(f"{normalized}/api/v1/auth/device/start")
        started_response.raise_for_status()
        started = started_response.json()
        typer.echo(f"Open {started['verification_uri']} and enter code {started['user_code']}.")
        if not no_browser:
            webbrowser.open(started["verification_uri_complete"])
        interval = int(started["interval"])
        deadline = time.monotonic() + int(started["expires_in"])
        while time.monotonic() < deadline:
            time.sleep(interval)
            polled = client.post(
                f"{normalized}/api/v1/auth/device/poll",
                json={"device_code": started["device_code"]},
            )
            polled.raise_for_status()
            payload = polled.json()
            if payload.get("status") == "authorization_pending":
                continue
            if payload.get("status") == "slow_down":
                interval += 5
                continue
            refresh_token = payload.get("refresh_token")
            if not isinstance(refresh_token, str):
                raise RuntimeError("platform returned an invalid device-login response")
            _store_refresh(normalized, refresh_token)
            target = _config_path()
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                json.dumps(
                    {"server": normalized, "principal_id": payload["principal_id"]}, indent=2
                )
                + "\n",
                encoding="utf-8",
            )
            typer.echo(f"Authenticated to {normalized}.")
            return
    raise RuntimeError("device authorization expired")


@app.command()
def claim_installation(
    organization_name: Annotated[
        str, typer.Option("--organization", help="Initial organization name")
    ],
    bootstrap_token: Annotated[
        str,
        typer.Option(
            "--bootstrap-token",
            prompt="Bootstrap token",
            hide_input=True,
            help="One-time installation token; omit the option to enter it securely",
        ),
    ],
    server: Annotated[str | None, typer.Option("--server")] = None,
) -> None:
    """Claim a new installation for the authenticated administrator."""
    configured = server or _configured_server()
    if configured is None:
        raise typer.BadParameter("configure a server with login --server first")
    normalized = configured.rstrip("/")
    with httpx.Client(timeout=15) as client:
        session = _refresh_session(client, normalized)
        response = client.post(
            f"{normalized}/api/v1/setup/claim",
            headers={"Authorization": f"Bearer {session['access_token']}"},
            json={
                "organization_name": organization_name,
                "bootstrap_token": bootstrap_token,
            },
        )
        response.raise_for_status()
    payload = response.json()
    typer.echo(
        f"Installation claimed; organization={payload['organization_id']}, "
        f"role={payload['role']}."
    )


@app.command()
def doctor(
    server: Annotated[str | None, typer.Option("--server")] = None,
) -> None:
    """Check server reachability and CLI compatibility without displaying secrets."""
    configured = server or _configured_server()
    if configured is None:
        raise typer.BadParameter("configure a server with login --server first")
    with httpx.Client(timeout=10) as client:
        session = _refresh_session(client, configured)
        response = client.get(
            f"{configured.rstrip('/')}/api/v1/client-releases/recommended",
            headers={
                "Authorization": f"Bearer {session['access_token']}",
                "X-Homebrew-MLflow-Client-Version": __version__,
            },
        )
        response.raise_for_status()
    release = response.json()["release"]
    compatible = Version(__version__) in SpecifierSet(release["compatible_versions"])
    typer.echo(
        f"Server reachable; installed={__version__}, "
        f"recommended={release['recommended_version']}, "
        f"compatible={release['compatible_versions']}"
    )
    if not compatible:
        typer.echo("Installed CLI version is not compatible with this server.", err=True)
        raise typer.Exit(2)
    checks = (("git", ["git", "--version"]), ("dvc", ["dvc", "--version"]))
    for name, command in checks:
        available = shutil.which(command[0]) is not None
        if available:
            result = subprocess.run(command, check=False, capture_output=True, text=True)
            available = result.returncode == 0
        typer.echo(f"{name}={'ok' if available else 'unavailable'}")


@app.command()
def logout(
    server: Annotated[str | None, typer.Option("--server")] = None,
    all_sessions: Annotated[bool, typer.Option("--all")] = False,
) -> None:
    """Revoke the current platform session and remove it from the OS keyring."""
    configured = server or _configured_server()
    if configured is None:
        raise typer.BadParameter("no configured server")
    token = keyring.get_password("homebrew-mlflow", configured)
    if token is not None:
        with httpx.Client(timeout=15) as client:
            normalized = configured.rstrip("/")
            if all_sessions:
                session = _refresh_session(client, normalized)
                response = client.post(
                    f"{normalized}/api/v1/auth/revoke-all",
                    headers={"Authorization": f"Bearer {session['access_token']}"},
                )
            else:
                response = client.post(
                    f"{normalized}/api/v1/auth/logout",
                    json={"refresh_token": token},
                )
            response.raise_for_status()
        keyring.delete_password("homebrew-mlflow", configured)
    typer.echo("Platform session revoked. Infisical login was not changed.")


@credentials_app.command("dvc")
def dvc_credentials(
    project: Annotated[str, typer.Option("--project")],
    server: Annotated[str | None, typer.Option("--server")] = None,
    recovery_run: Annotated[str | None, typer.Option("--recovery-run")] = None,
) -> None:
    """Emit the AWS credential_process JSON contract for native DVC."""
    configured = server or _configured_server()
    if configured is None:
        try:
            configured = _repository_context()["server"]
        except RuntimeError as error:
            raise typer.BadParameter("configure a server with login first") from error
    normalized = configured.rstrip("/")
    with httpx.Client(timeout=15) as client:
        session = _refresh_session(client, normalized)
        exchange = client.post(
            f"{normalized}/api/v1/auth/exchange",
            headers={"Authorization": f"Bearer {session['access_token']}"},
            json={
                "audience": "dvc-credentials",
                "project_id": project,
                "scopes": ["dvc_transfer"],
            },
        )
        exchange.raise_for_status()
        scoped_token = exchange.json().get("access_token")
        if not isinstance(scoped_token, str):
            raise RuntimeError("platform returned an invalid DVC access token")
        issued = client.post(
            f"{normalized}/api/v1/projects/{project}/dvc-credentials",
            headers={"Authorization": f"Bearer {scoped_token}"},
            params={"recovery_run_id": recovery_run} if recovery_run is not None else None,
        )
        issued.raise_for_status()
    payload = issued.json()
    required = {"Version", "AccessKeyId", "SecretAccessKey", "SessionToken", "Expiration"}
    if set(payload) != required:
        raise RuntimeError("platform returned an invalid AWS credential_process response")
    typer.echo(json.dumps(payload, ensure_ascii=True, separators=(",", ":")))


@artifact_app.command("pointer")
def artifact_pointer(
    version: Annotated[str, typer.Option("--version")],
    output: Annotated[Path, typer.Option("--output")],
    recovery_run: Annotated[str | None, typer.Option("--recovery-run")] = None,
    server: Annotated[str | None, typer.Option("--server")] = None,
    force: Annotated[bool, typer.Option("--force")] = False,
) -> None:
    """Download one authenticated standard .dvc pointer without artifact bytes."""
    if output.suffix != ".dvc":
        raise typer.BadParameter("--output must end with .dvc")
    if output.exists() and not force:
        raise typer.BadParameter("output exists; pass --force to replace it")
    configured = server or _configured_server()
    if configured is None:
        try:
            configured = _repository_context()["server"]
        except RuntimeError as error:
            raise typer.BadParameter("configure a server with login first") from error
    normalized = configured.rstrip("/")
    with httpx.Client(timeout=30) as client:
        session = _refresh_session(client, normalized)
        response = client.get(
            f"{normalized}/api/v1/artifact-versions/{version}/pointer",
            headers={"Authorization": f"Bearer {session['access_token']}"},
            params={"recovery_run_id": recovery_run} if recovery_run is not None else None,
        )
        response.raise_for_status()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(response.content)
    typer.echo(f"Wrote immutable pointer {output}")


@publication_app.command("submit")
def submit_publication(
    commit_sha: Annotated[str, typer.Option("--commit-sha")],
    artifact_id: Annotated[str, typer.Option("--artifact-id")],
    output: Annotated[str, typer.Option("--out")],
    pipeline: Annotated[str | None, typer.Option("--pipeline")] = None,
    stage: Annotated[str | None, typer.Option("--stage")] = None,
    dvc_file: Annotated[str | None, typer.Option("--dvc-file")] = None,
    run_id: Annotated[str | None, typer.Option("--run-id")] = None,
) -> None:
    """Submit committed DVC metadata for server-side publication validation."""
    repository = _repository_context()
    if dvc_file is not None:
        if pipeline is not None or stage is not None:
            raise typer.BadParameter("--dvc-file cannot be combined with pipeline options")
        selector = {"kind": "standalone-output", "dvc_file": dvc_file, "output": output}
    else:
        if pipeline is None or stage is None:
            raise typer.BadParameter("--pipeline and --stage are required together")
        selector = {
            "kind": "pipeline-output",
            "pipeline_file": pipeline,
            "stage": stage,
            "output": output,
        }
    server = repository["server"].rstrip("/")
    with httpx.Client(timeout=30) as client:
        session = _refresh_session(client, server)
        exchange = client.post(
            f"{server}/api/v1/auth/exchange",
            headers={"Authorization": f"Bearer {session['access_token']}"},
            json={
                "audience": "publication",
                "project_id": repository["project_id"],
                "scopes": ["publish"],
            },
        )
        exchange.raise_for_status()
        token = exchange.json().get("access_token")
        if not isinstance(token, str):
            raise RuntimeError("platform returned an invalid publication token")
        response = client.post(
            f"{server}/api/v1/projects/{repository['project_id']}/publication-operations",
            headers={
                "Authorization": f"Bearer {token}",
                "Idempotency-Key": secrets.token_urlsafe(24),
            },
            json={
                "artifact_id": artifact_id,
                "repository_id": repository["repository_id"],
                "commit_sha": commit_sha,
                "selector": selector,
                "run_id": run_id,
                "client": {"name": "homebrew-mlflow", "version": __version__},
            },
        )
        response.raise_for_status()
        operation = response.json()
        typer.echo(f"Publication {operation['operation_id']} queued.")
        with client.stream(
            "GET",
            f"{server}{operation['events_url']}",
            headers={"Authorization": f"Bearer {token}", "Accept": "text/event-stream"},
            timeout=None,
        ) as events:
            events.raise_for_status()
            for line in events.iter_lines():
                if line.startswith("event: "):
                    event_name = line.removeprefix("event: ")
                    typer.echo(event_name)
                    if event_name == "operation.failed":
                        raise typer.Exit(1)


@app.command(context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def run(
    context: typer.Context,
    experiment: Annotated[str, typer.Option("--experiment")],
    input_version: Annotated[list[str] | None, typer.Option("--input-version")] = None,
    pipeline_version: Annotated[str | None, typer.Option("--pipeline-version")] = None,
    environment: Annotated[str | None, typer.Option("--environment")] = None,
    infisical_project: Annotated[str | None, typer.Option("--infisical-project")] = None,
    infisical_environment: Annotated[str, typer.Option("--infisical-environment")] = "dev",
    infisical_path: Annotated[str, typer.Option("--infisical-path")] = "/",
) -> None:
    """Run a researcher-supplied command locally and record its lifecycle."""
    command = list(context.args)
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        raise typer.BadParameter("a command is required after --")
    repository = _repository_context()
    server = repository["server"].rstrip("/")
    with httpx.Client(timeout=15) as client:
        session = _refresh_session(client, server)
        created = client.post(
            f"{server}/api/v1/projects/{repository['project_id']}/runs",
            headers={"Authorization": f"Bearer {session['access_token']}"},
            json={
                "repository_id": repository["repository_id"],
                "experiment_name": experiment,
                "command": command,
                "pipeline_version_id": pipeline_version,
                "environment_specification_id": environment,
            },
        )
        created.raise_for_status()
        run_record = created.json()
    run_id = run_record["id"]
    logging_token = run_record.get("logging_token")
    if not isinstance(logging_token, str):
        raise RuntimeError("platform did not return a Run-scoped logging token")
    typer.echo(f"Run {run_id} started.")

    stop = threading.Event()
    heartbeat_error: list[str] = []

    def send_heartbeats() -> None:
        while not stop.wait(30):
            try:
                with httpx.Client(timeout=15) as heartbeat_client:
                    heartbeat_session = _refresh_session(heartbeat_client, server)
                    response = heartbeat_client.post(
                        f"{server}/api/v1/runs/{run_id}/heartbeat",
                        headers={"Authorization": f"Bearer {heartbeat_session['access_token']}"},
                    )
                    response.raise_for_status()
            except Exception as error:
                heartbeat_error.append(type(error).__name__)
                return

    heartbeat_thread = threading.Thread(target=send_heartbeats, daemon=True)
    heartbeat_thread.start()
    child_environment = os.environ.copy()
    child_environment.update(
        {
            "HOMEBREW_MLFLOW_RUN_ID": run_id,
            "HOMEBREW_MLFLOW_PROJECT_ID": repository["project_id"],
            "HOMEBREW_MLFLOW_REPOSITORY_ID": repository["repository_id"],
            "HOMEBREW_MLFLOW_SERVER": server,
            "MLFLOW_TRACKING_URI": f"{server}/mlflow",
            "MLFLOW_TRACKING_TOKEN": logging_token,
            "MLFLOW_RUN_ID": run_id,
            "MLFLOW_EXPERIMENT_ID": str(run_record["experiment_id"]),
        }
    )
    interrupted = False
    child_command = command
    secret_context: dict[str, str] | None = None
    if infisical_project is not None:
        if shutil.which("infisical") is None:
            raise RuntimeError("Infisical CLI is required for this Run")
        secret_context = {
            "project_id": infisical_project,
            "environment": infisical_environment,
            "path": infisical_path,
        }
        child_command = [
            "infisical",
            "run",
            "--projectId",
            infisical_project,
            "--env",
            infisical_environment,
            "--path",
            infisical_path,
            "--",
            *command,
        ]
    try:
        completed = subprocess.run(child_command, check=False, env=child_environment)  # noqa: S603
        exit_code = completed.returncode
    except KeyboardInterrupt:
        interrupted = True
        exit_code = 130
    finally:
        stop.set()
        heartbeat_thread.join(timeout=5)

    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    commit_sha = commit.stdout.strip() if commit.returncode == 0 else None
    status_value = "interrupted" if interrupted else ("succeeded" if exit_code == 0 else "failed")
    with httpx.Client(timeout=15) as client:
        final_session = _refresh_session(client, server)
        finalized = client.post(
            f"{server}/api/v1/runs/{run_id}/finalize",
            headers={"Authorization": f"Bearer {final_session['access_token']}"},
            json={
                "exit_code": exit_code,
                "status": status_value,
                "git_commit_sha": commit_sha,
                "evidence": {
                    "heartbeat_error": heartbeat_error[0] if heartbeat_error else None,
                    "client_version": __version__,
                    "python_version": platform.python_version(),
                    "input_artifact_version_ids": input_version or [],
                    "secret_context": secret_context,
                },
            },
        )
        finalized.raise_for_status()
    typer.echo(f"Run {run_id} finalized as {status_value}.")
    if exit_code:
        raise typer.Exit(exit_code)


if __name__ == "__main__":
    app()
