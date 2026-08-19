from __future__ import annotations

import hashlib
import json
import math
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import webbrowser
from contextlib import suppress
from pathlib import Path
from typing import Annotated, Any, cast
from urllib.parse import urlsplit

import boto3  # type: ignore[import-untyped]
import httpx
import keyring
import typer
from packaging.specifiers import SpecifierSet
from packaging.version import Version

from . import __version__
from .repository import (
    DvcConfiguration,
    install_dvc_profile,
    prepare_repository_template_upgrade,
    read_repository_dvc_configuration,
    reconcile_repository_configuration,
    repository_root,
)
from .runtime import capture_runtime, resolve_runtime

app = typer.Typer(no_args_is_help=True, pretty_exceptions_show_locals=False)
credentials_app = typer.Typer(no_args_is_help=True, hidden=True)
app.add_typer(credentials_app, name="credentials")
publication_app = typer.Typer(no_args_is_help=True)
app.add_typer(publication_app, name="publication")
artifact_app = typer.Typer(no_args_is_help=True)
app.add_typer(artifact_app, name="artifact")
artifact_alias_app = typer.Typer(no_args_is_help=True)
artifact_app.add_typer(artifact_alias_app, name="alias")
project_app = typer.Typer(no_args_is_help=True)
app.add_typer(project_app, name="project")
repository_app = typer.Typer(no_args_is_help=True)
app.add_typer(repository_app, name="repository")
_HEARTBEAT_INTERVAL_SECONDS = 30


def _config_path() -> Path:
    return Path(typer.get_app_dir("homebrew-mlflow")) / "config.json"


def _pending_run_directory() -> Path:
    return Path(typer.get_app_dir("homebrew-mlflow")) / "pending-runs"


def _write_private_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.chmod(temporary_name, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(payload, output, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        with suppress(OSError):
            os.close(descriptor)
        Path(temporary_name).unlink(missing_ok=True)
        raise


def _replace_token_file(path: Path, token: str) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.chmod(temporary_name, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(token)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        with suppress(OSError):
            os.close(descriptor)
        Path(temporary_name).unlink(missing_ok=True)
        raise


def _finalization_journal_path(server: str, run_id: str) -> Path:
    server_key = hashlib.sha256(server.encode("utf-8")).hexdigest()[:16]
    return _pending_run_directory() / server_key / f"{run_id}.json"


class _RetryableFinalization(RuntimeError):
    pass


def _raise_for_finalization_status(response: httpx.Response) -> None:
    if response.status_code in {408, 425, 429} or response.status_code >= 500:
        raise _RetryableFinalization(f"HTTP {response.status_code}")
    response.raise_for_status()


def _recover_finalization(path: Path, *, run_token: str | None = None) -> dict[str, Any]:
    journal = json.loads(path.read_text(encoding="utf-8"))
    if (
        journal.get("schema") != 1
        or not isinstance(journal.get("server"), str)
        or not isinstance(journal.get("run_id"), str)
        or not isinstance(journal.get("project_id"), str)
        or not isinstance(journal.get("idempotency_key"), str)
        or not isinstance(journal.get("finalization"), dict)
    ):
        raise RuntimeError("pending Run journal is invalid")
    server = journal["server"].rstrip("/")
    run_id = journal["run_id"]
    delays = (0, 1, 2, 4, 8, 16, 30)
    last_error: Exception | None = None
    for attempt, delay in enumerate(delays):
        if delay:
            time.sleep(delay)
        try:
            with httpx.Client(timeout=15) as client:
                pipeline = journal.get("pipeline_resolution")
                platform_headers: dict[str, str] | None = None
                if (
                    journal["finalization"].get("pipeline_version_id") is None
                    and isinstance(pipeline, dict)
                ):
                    platform_headers = _project_headers(_refresh_session(client, server))
                    response = client.put(
                        f"{server}/api/v1/projects/{journal['project_id']}"
                        "/pipeline-versions/resolve",
                        headers=platform_headers,
                        json=pipeline,
                    )
                    _raise_for_finalization_status(response)
                    pipeline_id = response.json().get("id")
                    if not isinstance(pipeline_id, str):
                        raise RuntimeError("platform returned an invalid pipeline version")
                    journal["finalization"]["pipeline_version_id"] = pipeline_id
                    _write_private_json(path, journal)
                if run_token is not None:
                    finalization_headers = {"Authorization": f"Bearer {run_token}"}
                else:
                    finalization_headers = platform_headers or _project_headers(
                        _refresh_session(client, server)
                    )
                response = client.post(
                    f"{server}/api/v1/runs/{run_id}/finalize",
                    headers={
                        **finalization_headers,
                        "Idempotency-Key": journal["idempotency_key"],
                    },
                    json=journal["finalization"],
                )
                _raise_for_finalization_status(response)
                result = cast(dict[str, Any], response.json())
            path.unlink(missing_ok=True)
            return result
        except httpx.HTTPStatusError as error:
            if (
                error.response.status_code not in {408, 425, 429}
                and error.response.status_code < 500
            ):
                raise
            last_error = error
            if attempt == len(delays) - 1:
                break
        except (httpx.TransportError, _RetryableFinalization) as error:
            last_error = error
            if attempt == len(delays) - 1:
                break
    assert last_error is not None
    raise RuntimeError(
        f"Run finalization remains unavailable after {len(delays)} attempts "
        f"({type(last_error).__name__})"
    ) from last_error


def _find_finalization_journal(run_id: str) -> Path:
    matches = list(_pending_run_directory().glob(f"*/{run_id}.json"))
    if len(matches) != 1:
        raise RuntimeError(f"found {len(matches)} pending finalizations for Run {run_id}")
    return matches[0]


def _send_run_heartbeats(
    stop: threading.Event,
    server: str,
    run_id: str,
    token_path: Path,
    errors: list[str],
) -> None:
    while not stop.wait(_HEARTBEAT_INTERVAL_SECONDS):
        try:
            with httpx.Client(timeout=15) as heartbeat_client:
                logging_token = token_path.read_text(encoding="utf-8").strip()
                if not logging_token:
                    raise RuntimeError("Run logging token file is empty")
                response = heartbeat_client.post(
                    f"{server}/api/v1/runs/{run_id}/heartbeat",
                    headers={"Authorization": f"Bearer {logging_token}"},
                )
                response.raise_for_status()
                refreshed_logging_token = response.json().get("logging_token")
                if not isinstance(refreshed_logging_token, str):
                    raise RuntimeError("platform omitted the refreshed Run logging token")
                _replace_token_file(token_path, refreshed_logging_token)
        except httpx.HTTPStatusError as error:
            errors.append(f"HTTP_{error.response.status_code}")
            if error.response.status_code < 500 and error.response.status_code not in {
                408,
                425,
                429,
            }:
                return
        except Exception as error:
            errors.append(type(error).__name__)


def _store_refresh(server: str, token: str) -> None:
    keyring.set_password("homebrew-mlflow", server, token)


def _configured_server() -> str | None:
    if not _config_path().exists():
        return None
    value = json.loads(_config_path().read_text(encoding="utf-8")).get("server")
    return value if isinstance(value, str) else None


def _repository_context(start: Path | None = None) -> dict[str, str]:
    candidate = repository_root(start) / ".homebrew-mlflow.json"
    payload = json.loads(candidate.read_text(encoding="utf-8"))
    required = ("server", "project_id", "repository_id")
    if not all(isinstance(payload.get(key), str) for key in required):
        raise RuntimeError("invalid .homebrew-mlflow.json repository context")
    return {key: payload[key] for key in required}


def _git_output(*arguments: str, root: Path | None = None) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Git command failed: git {' '.join(arguments)}")
    return result.stdout.strip()


def _committed_upstream_state(root: Path) -> str:
    if _git_output("status", "--porcelain", root=root):
        raise RuntimeError("repository has uncommitted changes")
    commit = _git_output("rev-parse", "HEAD", root=root)
    upstream = _git_output("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}", root=root)
    if _git_output("rev-list", "--count", f"{upstream}..HEAD", root=root) != "0":
        raise RuntimeError("current commit has not been pushed to its upstream branch")
    return commit


def _dvc_experiment_refs(root: Path) -> dict[str, str]:
    refs: dict[str, str] = {}
    output = _git_output(
        "for-each-ref",
        "refs/exps",
        "--format=%(refname) %(objectname)",
        root=root,
    )
    for line in output.splitlines():
        ref, separator, revision = line.partition(" ")
        if separator and not ref.startswith("refs/exps/exec/"):
            refs[ref] = revision
    return refs


def _changed_dvc_experiment(
    root: Path,
    base_commit: str,
    before: dict[str, str],
    after: dict[str, str],
) -> tuple[str | None, list[str], list[str]]:
    changed = sorted(ref for ref, revision in after.items() if before.get(ref) != revision)
    revisions = sorted({after[ref] for ref in changed})
    if not revisions:
        return None, changed, []
    if len(revisions) != 1:
        return None, changed, ["ambiguous_dvc_experiments"]
    revision = revisions[0]
    merge_base = _git_output("merge-base", base_commit, revision, root=root)
    if len(revision) != 40 or merge_base != base_commit:
        return None, changed, ["invalid_dvc_experiment_ancestry"]
    return revision, changed, []


def _git_blob_evidence(root: Path, revision: str, path: str) -> dict[str, object] | None:
    result = subprocess.run(
        ["git", "show", f"{revision}:{path}"],
        cwd=root,
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        return None
    evidence: dict[str, object] = {
        "path": path,
        "sha256": hashlib.sha256(result.stdout).hexdigest(),
    }
    if path == "dvc.lock":
        evidence["candidate_output_paths"] = _dvc_lock_output_paths(result.stdout)
    return evidence


def _dvc_lock_output_paths(content: bytes) -> list[str]:
    candidates: set[str] = set()
    outputs_indent: int | None = None
    for line in content.decode("utf-8", errors="replace").splitlines():
        stripped = line.lstrip()
        indent = len(line) - len(stripped)
        if stripped == "outs:":
            outputs_indent = indent
            continue
        if outputs_indent is None:
            continue
        match = re.fullmatch(r"-\s+path:\s*(.+)", stripped)
        if match is not None and indent >= outputs_indent:
            candidates.add(match.group(1).strip().strip("'\""))
            continue
        if stripped and indent <= outputs_indent:
            outputs_indent = None
            continue
    return sorted(candidates)


def _resolve_environment(
    client: httpx.Client,
    server: str,
    headers: dict[str, str],
    project_id: str,
    name: str,
    kind: str,
    document: dict[str, object],
) -> str:
    response = client.put(
        f"{server}/api/v1/projects/{project_id}/environment-specifications/resolve",
        headers=headers,
        json={"name": name, "kind": kind, "document": document},
    )
    response.raise_for_status()
    identifier = response.json().get("id")
    if not isinstance(identifier, str):
        raise RuntimeError("platform returned an invalid environment specification")
    return identifier


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


def _project_slug(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")


def _project_server(server: str | None) -> str:
    configured = server or _configured_server()
    if configured is None:
        raise typer.BadParameter("configure a server with login --server first")
    return configured.rstrip("/")


def _project_headers(session: dict[str, object]) -> dict[str, str]:
    access = session.get("access_token")
    if not isinstance(access, str):
        raise RuntimeError("platform returned an invalid access token")
    return {"Authorization": f"Bearer {access}"}


def _resolve_project(
    client: httpx.Client,
    server: str,
    headers: dict[str, str],
    reference: str,
) -> dict[str, object]:
    response = client.get(f"{server}/api/v1/projects", headers=headers)
    response.raise_for_status()
    matches = [
        value
        for value in response.json()
        if value.get("id") == reference or value.get("slug") == reference
    ]
    if len(matches) != 1:
        raise typer.BadParameter(f"project {reference!r} was not found or is ambiguous")
    return cast(dict[str, object], matches[0])


def _project_repositories(
    client: httpx.Client,
    server: str,
    headers: dict[str, str],
    project_id: str,
) -> list[dict[str, object]]:
    response = client.get(
        f"{server}/api/v1/projects/{project_id}/repositories", headers=headers
    )
    response.raise_for_status()
    return cast(list[dict[str, object]], response.json())


def _wait_for_repository(
    client: httpx.Client,
    server: str,
    headers: dict[str, str],
    project_id: str,
    repository_id: str,
    timeout: int,
) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    last_state = ""
    while time.monotonic() < deadline:
        repositories = _project_repositories(client, server, headers, project_id)
        repository = next(
            (value for value in repositories if value.get("id") == repository_id), None
        )
        if repository is None:
            raise RuntimeError("created repository disappeared from the platform")
        state = str(repository.get("state", "unknown"))
        if state != last_state:
            typer.echo(f"Repository provisioning: {state}")
            last_state = state
        if state == "active":
            return repository
        if state == "failed":
            code = str(repository.get("failure_code") or "unknown")
            raise RuntimeError(f"repository provisioning failed: {code}")
        time.sleep(2)
    raise RuntimeError(f"repository provisioning did not finish within {timeout} seconds")


def _print_repository(repository: dict[str, object]) -> None:
    typer.echo(
        f"repository={repository.get('id')} state={repository.get('state')} "
        f"gitlab={repository.get('web_url') or '-'}"
    )
    typer.echo(f"ssh={repository.get('ssh_clone_url') or '-'}")
    typer.echo(f"https={repository.get('http_clone_url') or '-'}")


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


@project_app.command("create")
def project_create(
    name: Annotated[str, typer.Option("--name", help="Research project name")],
    slug: Annotated[str | None, typer.Option("--slug")] = None,
    clone_to: Annotated[Path | None, typer.Option("--clone-to")] = None,
    no_wait: Annotated[bool, typer.Option("--no-wait")] = False,
    timeout: Annotated[int, typer.Option("--timeout", min=1)] = 300,
    server: Annotated[str | None, typer.Option("--server")] = None,
) -> None:
    """Create a research project and its seeded default repository."""
    if no_wait and clone_to is not None:
        raise typer.BadParameter("--clone-to cannot be combined with --no-wait")
    normalized_slug = (slug or _project_slug(name)).strip().lower()
    if re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", normalized_slug) is None:
        raise typer.BadParameter(
            "slug must contain lowercase ASCII letters, digits, and single hyphens"
        )
    normalized = _project_server(server)
    with httpx.Client(timeout=15) as client:
        session = _refresh_session(client, normalized)
        headers = _project_headers(session)
        organization_response = client.get(
            f"{normalized}/api/v1/organization", headers=headers
        )
        organization_response.raise_for_status()
        organization_id = organization_response.json()["id"]
        response = client.post(
            f"{normalized}/api/v1/projects",
            headers=headers,
            json={
                "organization_id": organization_id,
                "name": name,
                "slug": normalized_slug,
            },
        )
        response.raise_for_status()
        created = cast(dict[str, object], response.json())
        repository = cast(dict[str, object], created["default_repository"])
        project_id = str(created["id"])
        repository_id = str(repository["id"])
        typer.echo(
            f"Created project={project_id} slug={normalized_slug} "
            f"repository={repository_id}."
        )
        if no_wait:
            return
        repository = _wait_for_repository(
            client, normalized, headers, project_id, repository_id, timeout
        )
    _print_repository(repository)
    if clone_to is not None:
        ssh_url = repository.get("ssh_clone_url")
        if not isinstance(ssh_url, str) or not ssh_url:
            raise RuntimeError("active repository omitted its SSH clone URL")
        result = subprocess.run(
            ["git", "clone", ssh_url, str(clone_to)], check=False
        )
        if result.returncode != 0:
            raise RuntimeError(
                "project was created, but git clone failed; verify your GitLab SSH key"
            )
        profile, path = install_dvc_profile(clone_to.resolve())
        typer.echo(f"Configured DVC credential profile {profile} in {path}.")


@project_app.command("list")
def project_list(
    server: Annotated[str | None, typer.Option("--server")] = None,
) -> None:
    """List research projects visible to the current principal."""
    normalized = _project_server(server)
    with httpx.Client(timeout=15) as client:
        headers = _project_headers(_refresh_session(client, normalized))
        response = client.get(f"{normalized}/api/v1/projects", headers=headers)
        response.raise_for_status()
        projects = cast(list[dict[str, object]], response.json())
    if not projects:
        typer.echo("No research projects.")
        return
    for project in projects:
        typer.echo(
            f"{project.get('id')}\t{project.get('slug')}\t{project.get('state')}\t"
            f"{project.get('name')}"
        )


@project_app.command("status")
def project_status(
    project: Annotated[str, typer.Argument(help="Project ID or exact slug")],
    server: Annotated[str | None, typer.Option("--server")] = None,
) -> None:
    """Show project and repository provisioning status."""
    normalized = _project_server(server)
    with httpx.Client(timeout=15) as client:
        headers = _project_headers(_refresh_session(client, normalized))
        selected = _resolve_project(client, normalized, headers, project)
        repositories = _project_repositories(
            client, normalized, headers, str(selected["id"])
        )
    typer.echo(
        f"project={selected.get('id')} slug={selected.get('slug')} "
        f"state={selected.get('state')}"
    )
    for repository in repositories:
        _print_repository(repository)
        if repository.get("failure_code"):
            typer.echo(f"failure={repository['failure_code']}")


@project_app.command("retry")
def project_retry(
    project: Annotated[str, typer.Argument(help="Project ID or exact slug")],
    no_wait: Annotated[bool, typer.Option("--no-wait")] = False,
    timeout: Annotated[int, typer.Option("--timeout", min=1)] = 300,
    server: Annotated[str | None, typer.Option("--server")] = None,
) -> None:
    """Retry the failed default-repository provisioning operation."""
    normalized = _project_server(server)
    with httpx.Client(timeout=15) as client:
        headers = _project_headers(_refresh_session(client, normalized))
        selected = _resolve_project(client, normalized, headers, project)
        project_id = str(selected["id"])
        failed = [
            value
            for value in _project_repositories(client, normalized, headers, project_id)
            if value.get("state") == "failed"
        ]
        if len(failed) != 1:
            raise typer.BadParameter("project must have exactly one failed repository")
        repository_id = str(failed[0]["id"])
        response = client.post(
            f"{normalized}/api/v1/projects/{project_id}/repositories/"
            f"{repository_id}/retry-provisioning",
            headers=headers,
        )
        response.raise_for_status()
        typer.echo(f"Retry queued for repository={repository_id}.")
        if no_wait:
            return
        repository = _wait_for_repository(
            client, normalized, headers, project_id, repository_id, timeout
        )
    _print_repository(repository)


@repository_app.command("configure")
def repository_configure() -> None:
    """Reconcile managed settings and safely upgrade repository instructions."""
    root = repository_root()
    template_upgrade = prepare_repository_template_upgrade(root)
    repository = _repository_context(root)
    server = repository["server"].rstrip("/")
    with httpx.Client(timeout=15) as client:
        session = _refresh_session(client, server)
        response = client.get(
            f"{server}/api/v1/projects/{repository['project_id']}/dvc-configuration",
            headers=_project_headers(session),
        )
        response.raise_for_status()
    configuration = DvcConfiguration.from_payload(response.json())
    profile, path, changed = reconcile_repository_configuration(root, configuration)
    template_changed = template_upgrade.apply()
    changed = (*changed, *template_changed)
    typer.echo(f"Configured DVC credential profile {profile} in {path}.")
    if changed:
        for changed_path in changed:
            typer.echo(f"Updated repository file {changed_path.relative_to(root).as_posix()}.")
        typer.echo("Review and commit the updated repository files before starting a Run.")
    else:
        typer.echo("Repository-managed DVC configuration is current.")


def _first_line(value: str) -> str:
    lines = value.strip().splitlines()
    return lines[0] if lines else ""


def _safe_error(error: BaseException) -> str:
    if isinstance(error, RuntimeError):
        return str(error).replace("\n", " ")
    response = getattr(error, "response", None)
    if isinstance(response, dict):
        code = response.get("Error", {}).get("Code")
        if isinstance(code, str):
            return f"storage_error={code}"
    return type(error).__name__


def _runtime_dvc_version_command(root: Path, kind: str, name: str) -> list[str]:
    if kind == "uv":
        if shutil.which("uv") is None:
            raise RuntimeError("uv is required by the selected environment")
        return ["uv", "run", "--frozen", "--", "dvc", "--version"]
    if kind == "pip":
        candidates = (
            root / ".venv" / "Scripts" / "dvc.exe",
            root / ".venv" / "bin" / "dvc",
        )
        executable = next((path for path in candidates if path.is_file()), None)
        if executable is None:
            raise RuntimeError("DVC is unavailable in the repository .venv")
        return [str(executable), "--version"]
    if kind == "conda":
        return ["conda", "run", "-n", name, "--no-capture-output", "dvc", "--version"]
    if kind == "system":
        return ["dvc", "--version"]
    raise RuntimeError("container environments require an explicit DVC diagnostic command")


def _runtime_mlflow_diagnostic_command(root: Path, kind: str, name: str) -> list[str]:
    module = "homebrew_mlflow.mlflow_plugins.diagnostics"
    if kind == "uv":
        if shutil.which("uv") is None:
            raise RuntimeError("uv is required by the selected environment")
        return ["uv", "run", "--frozen", "--", "python", "-m", module]
    if kind == "pip":
        candidates = (
            root / ".venv" / "Scripts" / "python.exe",
            root / ".venv" / "bin" / "python",
        )
        executable = next((path for path in candidates if path.is_file()), None)
        if executable is None:
            raise RuntimeError("Python is unavailable in the repository .venv")
        return [str(executable), "-m", module]
    if kind == "conda":
        return ["conda", "run", "-n", name, "--no-capture-output", "python", "-m", module]
    if kind == "system":
        return ["python", "-m", module]
    raise RuntimeError("container environments require an explicit MLflow diagnostic command")


def _runtime_dvc_capture_command(
    root: Path, kind: str, name: str, base_revision: str
) -> list[str]:
    arguments = [
        "dvc",
        "exp",
        "show",
        "--rev",
        base_revision,
        "--json",
        "--no-pager",
    ]
    if kind == "uv":
        if shutil.which("uv") is None:
            raise RuntimeError("uv is required by the selected environment")
        return ["uv", "run", "--frozen", "--", *arguments]
    if kind == "pip":
        candidates = (
            root / ".venv" / "Scripts" / "dvc.exe",
            root / ".venv" / "bin" / "dvc",
        )
        executable = next((path for path in candidates if path.is_file()), None)
        if executable is None:
            raise RuntimeError("DVC is unavailable in the repository .venv")
        return [str(executable), *arguments[1:]]
    if kind == "conda":
        return ["conda", "run", "-n", name, "--no-capture-output", *arguments]
    if kind == "system":
        return arguments
    raise RuntimeError("automatic DVC tracking capture is unavailable for container environments")


def _find_dvc_experiment(value: Any, revision: str) -> dict[str, Any] | None:
    if isinstance(value, dict):
        data = value.get("data")
        if value.get("rev") == revision and isinstance(data, dict):
            return cast(dict[str, Any], data)
        for child in value.values():
            match = _find_dvc_experiment(child, revision)
            if match is not None:
                return match
    elif isinstance(value, list):
        for child in value:
            match = _find_dvc_experiment(child, revision)
            if match is not None:
                return match
    return None


def _flatten_dvc_values(value: Any, prefix: str = "") -> list[tuple[str, Any]]:
    if isinstance(value, dict):
        flattened: list[tuple[str, Any]] = []
        for key in sorted(value):
            path = f"{prefix}.{key}" if prefix else str(key)
            flattened.extend(_flatten_dvc_values(value[key], path))
        return flattened
    return [(prefix, value)] if prefix else []


def _dvc_tracking_values(
    files: Any, *, metrics: bool
) -> tuple[list[dict[str, Any]], list[str]]:
    if not isinstance(files, dict):
        return [], ["DVC returned malformed tracking data"]
    candidates: list[tuple[str, str, Any]] = []
    warnings: list[str] = []
    for path, result in sorted(files.items()):
        if not isinstance(path, str) or not isinstance(result, dict):
            warnings.append("DVC returned a malformed tracking file entry")
            continue
        if result.get("error"):
            warnings.append(f"DVC could not read {path}")
            continue
        for key, value in _flatten_dvc_values(result.get("data")):
            candidates.append((path.replace("\\", "/"), key, value))
    counts: dict[str, int] = {}
    for _, key, _ in candidates:
        counts[key] = counts.get(key, 0) + 1
    values: list[dict[str, Any]] = []
    for path, key, value in candidates:
        normalized_key = key if counts[key] == 1 else f"{path}:{key}"
        if len(normalized_key) > 250 or normalized_key.startswith("homebrew."):
            warnings.append(f"Skipped invalid tracking key from {path}")
            continue
        if metrics:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            numeric = float(value)
            if not math.isfinite(numeric):
                warnings.append(f"Skipped non-finite metric {normalized_key}")
                continue
            values.append({"key": normalized_key, "value": numeric})
        elif isinstance(value, (str, int, float, bool)):
            rendered = value if isinstance(value, str) else json.dumps(value)
            if len(rendered) > 6000:
                warnings.append(f"Skipped oversized parameter {normalized_key}")
                continue
            values.append({"key": normalized_key, "value": rendered})
    return values[:1000], warnings


def _capture_dvc_tracking(
    root: Path,
    selection_kind: str,
    selection_name: str,
    base_revision: str,
    experiment_revision: str,
    server: str,
    run_id: str,
    run_token: str,
) -> dict[str, Any]:
    command = _runtime_dvc_capture_command(
        root, selection_kind, selection_name, base_revision
    )
    completed = subprocess.run(
        command, cwd=root, check=False, capture_output=True, text=True
    )
    if completed.returncode != 0:
        raise RuntimeError("DVC experiment tracking capture failed")
    experiment = _find_dvc_experiment(json.loads(completed.stdout), experiment_revision)
    if experiment is None:
        raise RuntimeError("resolved DVC experiment was absent from tracking capture")
    metrics, metric_warnings = _dvc_tracking_values(
        experiment.get("metrics"), metrics=True
    )
    parameters, parameter_warnings = _dvc_tracking_values(
        experiment.get("params"), metrics=False
    )
    headers = {"Authorization": f"Bearer {run_token}"}
    with httpx.Client(timeout=30) as client:
        current_response = client.get(
            f"{server}/api/v1/runs/{run_id}/tracking", headers=headers
        )
        current_response.raise_for_status()
        current = current_response.json()
        explicit_metrics = {item["key"] for item in current.get("metrics", [])}
        explicit_parameters = {item["key"] for item in current.get("parameters", [])}
        metrics = [item for item in metrics if item["key"] not in explicit_metrics]
        parameters = [
            item for item in parameters if item["key"] not in explicit_parameters
        ]
        timestamp_ms = int(time.time() * 1000)
        for metric in metrics:
            metric.update({"timestamp_ms": timestamp_ms, "step": 0})
        if metrics or parameters:
            response = client.post(
                f"{server}/api/v1/runs/{run_id}/tracking/batch",
                headers=headers,
                json={"metrics": metrics, "parameters": parameters, "tags": []},
            )
            response.raise_for_status()
    return {
        "status": "captured",
        "metrics_imported": len(metrics),
        "parameters_imported": len(parameters),
        "warnings": (metric_warnings + parameter_warnings)[:20],
    }


def _probe_dvc_remote(configuration: DvcConfiguration) -> None:
    parsed = urlsplit(configuration.remote_url)
    if parsed.scheme != "s3" or not parsed.netloc or not parsed.path.strip("/"):
        raise RuntimeError("platform returned an invalid project DVC remote URL")
    session = boto3.Session(profile_name=configuration.profile)
    client: Any = session.client(
        "s3", endpoint_url=configuration.endpoint_url, region_name="us-east-1"
    )
    client.list_objects_v2(
        Bucket=parsed.netloc,
        Prefix=f"{parsed.path.strip('/')}/",
        MaxKeys=1,
    )


def _doctor_infisical(
    report: Any,
    root: Path,
    server: str,
    headers: dict[str, str],
    project_id: str,
) -> None:
    executable = shutil.which("infisical")
    if executable is None:
        report("infisical", False, "CLI unavailable")
        return
    version = subprocess.run(
        [executable, "--version"], cwd=root, check=False, capture_output=True, text=True
    )
    if version.returncode != 0:
        report("infisical", False, "version check failed")
        return
    try:
        with httpx.Client(timeout=15) as client:
            response = client.get(
                f"{server}/api/v1/projects/{project_id}/secret-context", headers=headers
            )
            response.raise_for_status()
        context = response.json()
        if context.get("reconciliation_state") != "in_sync":
            raise RuntimeError("project secret context is not reconciled")
        access = subprocess.run(
            [
                executable,
                "run",
                "--projectId",
                str(context["infisical_project_id"]),
                "--env",
                str(context["environment_slug"]),
                "--path",
                str(context["secret_path"]),
                "--",
                sys.executable,
                "-c",
                "pass",
            ],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
        )
        if access.returncode != 0:
            raise RuntimeError("Infisical session or project access check failed")
        report("infisical", True, _first_line(version.stdout))
    except (RuntimeError, httpx.HTTPError, KeyError) as error:
        report("infisical", False, _safe_error(error))


@app.command()
def doctor(
    server: Annotated[str | None, typer.Option("--server")] = None,
) -> None:
    """Check platform and repository readiness without displaying secrets."""
    configured = server or _configured_server()
    if configured is None:
        raise typer.BadParameter("configure a server with login --server first")
    normalized = configured.rstrip("/")
    failures: list[str] = []

    def result(name: str, ok: bool, detail: str = "") -> None:
        suffix = f" {detail}" if detail else ""
        typer.echo(f"{name}={'ok' if ok else 'failed'}{suffix}")
        if not ok:
            failures.append(name)

    with httpx.Client(timeout=10) as client:
        session = _refresh_session(client, normalized)
        headers = _project_headers(session)
        release_response = client.get(
            f"{normalized}/api/v1/client-releases/recommended",
            headers={
                **headers,
                "X-Homebrew-MLflow-Client-Version": __version__,
            },
        )
        release_response.raise_for_status()
        release = release_response.json()["release"]
        result("platform_session", True)

    compatible = Version(__version__) in SpecifierSet(release["compatible_versions"])
    result(
        "cli_release",
        compatible,
        f"installed={__version__} recommended={release['recommended_version']} "
        f"compatible={release['compatible_versions']}",
    )

    try:
        root = repository_root()
        repository = _repository_context(root)
    except (RuntimeError, OSError, json.JSONDecodeError) as error:
        result("repository", False, _safe_error(error))
        typer.echo("readiness=failed")
        raise typer.Exit(2) from error

    with httpx.Client(timeout=10) as client:
        repositories = _project_repositories(
            client, normalized, headers, repository["project_id"]
        )
        mapped = next(
            (item for item in repositories if item.get("id") == repository["repository_id"]),
            None,
        )
        result(
            "repository",
            mapped is not None and mapped.get("state") == "active",
            f"project={repository['project_id']} repository={repository['repository_id']}",
        )
        configuration_response = client.get(
            f"{normalized}/api/v1/projects/{repository['project_id']}/dvc-configuration",
            headers=headers,
        )
        configuration_response.raise_for_status()
        canonical = DvcConfiguration.from_payload(configuration_response.json())
        mlflow_response = client.get(
            f"{normalized}/api/v1/diagnostics/mlflow", headers=headers
        )
        result(
            "mlflow_service",
            mlflow_response.is_success,
            f"status={mlflow_response.status_code}",
        )

    git_check = subprocess.run(
        ["git", "--version"], check=False, capture_output=True, text=True
    )
    result("git", git_check.returncode == 0, _first_line(git_check.stdout))

    try:
        selection = resolve_runtime(root, ["dvc", "--version"])
        dvc_command = _runtime_dvc_version_command(root, selection.kind, selection.name)
        dvc_check = subprocess.run(
            dvc_command, cwd=root, check=False, capture_output=True, text=True
        )
        result("dvc", dvc_check.returncode == 0, _first_line(dvc_check.stdout))
    except (RuntimeError, OSError) as error:
        selection = None
        result("dvc", False, _safe_error(error))

    if selection is not None:
        diagnostic_path: Path | None = None
        try:
            descriptor, diagnostic_name = tempfile.mkstemp(
                prefix="homebrew-mlflow-diagnostic-token-"
            )
            diagnostic_path = Path(diagnostic_name)
            with os.fdopen(descriptor, "w", encoding="utf-8") as token_file:
                token_file.write("diagnostic-invalid-token")
            diagnostic_environment = os.environ.copy()
            diagnostic_environment.update(
                {
                    "MLFLOW_TRACKING_URI": f"{normalized}/mlflow",
                    "MLFLOW_TRACKING_AUTH": "homebrew-token-file",
                    "MLFLOW_TRACKING_TOKEN_FILE": str(diagnostic_path),
                    "MLFLOW_HTTP_REQUEST_MAX_RETRIES": "0",
                }
            )
            diagnostic = subprocess.run(
                _runtime_mlflow_diagnostic_command(
                    root, selection.kind, selection.name
                ),
                cwd=root,
                check=False,
                capture_output=True,
                text=True,
                env=diagnostic_environment,
            )
            lines = set(diagnostic.stdout.splitlines())
            result("mlflow_client_auth", "mlflow_client_auth=ok" in lines)
            result(
                "mlflow_auth_boundary",
                diagnostic.returncode == 0 and "mlflow_auth_boundary=ok" in lines,
            )
        except (RuntimeError, OSError) as error:
            result("mlflow_client_auth", False, _safe_error(error))
            result("mlflow_auth_boundary", False, _safe_error(error))
        finally:
            if diagnostic_path is not None:
                diagnostic_path.unlink(missing_ok=True)

    configuration_matches = False
    try:
        local = read_repository_dvc_configuration(root, canonical.remote_name)
        configuration_matches = local == canonical
        if not configuration_matches:
            raise RuntimeError("run `homebrew-mlflow repository configure` to repair configuration")
        result("dvc_configuration", True, f"remote={canonical.remote_url}")
    except (RuntimeError, OSError, ValueError) as error:
        result("dvc_configuration", False, _safe_error(error))
    if configuration_matches:
        try:
            _probe_dvc_remote(canonical)
            result("dvc_remote", True)
        except Exception as error:  # credentials and SDK failures are normalized below
            result("dvc_remote", False, _safe_error(error))

    if selection is not None and selection.secrets_enabled:
        _doctor_infisical(result, root, normalized, headers, repository["project_id"])
    else:
        typer.echo("infisical=skipped secrets-disabled")

    if failures:
        typer.echo("readiness=failed")
        raise typer.Exit(2)
    typer.echo("readiness=ok")


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
    issued: httpx.Response | None = None
    try:
        with httpx.Client(timeout=httpx.Timeout(15, connect=5)) as client:
            for delay in (0.25, 1.0, None):
                try:
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
                        params=(
                            {"recovery_run_id": recovery_run}
                            if recovery_run is not None
                            else None
                        ),
                    )
                    issued.raise_for_status()
                    break
                except (httpx.ConnectError, httpx.ConnectTimeout):
                    if delay is None:
                        raise
                    time.sleep(delay)
    except (httpx.ConnectError, httpx.ConnectTimeout) as error:
        typer.echo(
            "DVC credential request failed after 3 connection attempts "
            f"({type(error).__name__}).",
            err=True,
        )
        raise typer.Exit(1) from None
    except httpx.HTTPError as error:
        typer.echo(
            f"DVC credential request failed ({type(error).__name__}).",
            err=True,
        )
        raise typer.Exit(1) from None
    if issued is None:
        raise RuntimeError("platform did not return DVC credentials")
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


@artifact_app.command("create")
def artifact_create(
    name: Annotated[str, typer.Argument(help="Artifact family name")],
    kind: Annotated[
        str, typer.Option(help="dataset, model, checkpoint, report, or generic")
    ] = "generic",
    description: Annotated[str | None, typer.Option()] = None,
) -> None:
    """Create an explicit, reusable artifact family in the current project."""
    repository = _repository_context()
    server = repository["server"].rstrip("/")
    with httpx.Client(timeout=15) as client:
        headers = _project_headers(_refresh_session(client, server))
        response = client.post(
            f"{server}/api/v1/projects/{repository['project_id']}/artifacts",
            headers=headers,
            json={"name": name, "kind": kind, "description": description},
        )
        response.raise_for_status()
    artifact = response.json()
    typer.echo(f"{artifact['id']}\t{artifact['name']}")


@artifact_app.command("list")
def artifact_list() -> None:
    """List artifact families in the current project."""
    repository = _repository_context()
    server = repository["server"].rstrip("/")
    with httpx.Client(timeout=15) as client:
        headers = _project_headers(_refresh_session(client, server))
        response = client.get(
            f"{server}/api/v1/projects/{repository['project_id']}/artifacts", headers=headers
        )
        response.raise_for_status()
    for artifact in response.json():
        typer.echo(f"{artifact['id']}\t{artifact['kind']}\t{artifact['name']}")


def _resolve_artifact(
    client: httpx.Client, server: str, headers: dict[str, str], project_id: str, reference: str
) -> str:
    response = client.get(f"{server}/api/v1/projects/{project_id}/artifacts", headers=headers)
    response.raise_for_status()
    matches = [
        value
        for value in response.json()
        if value.get("id") == reference or value.get("name", "").casefold() == reference.casefold()
    ]
    if len(matches) != 1:
        raise typer.BadParameter(
            f"artifact {reference!r} was not found or is ambiguous; create it explicitly first"
        )
    return str(matches[0]["id"])


@artifact_app.command("classify")
def artifact_classify(
    artifact: Annotated[str, typer.Argument(help="Artifact family ID or name")],
    kind: Annotated[str, typer.Option(help="New Artifact kind")],
    description: Annotated[str | None, typer.Option()] = None,
) -> None:
    """Set Maintainer-managed Artifact catalog metadata."""
    repository = _repository_context()
    server = repository["server"].rstrip("/")
    with httpx.Client(timeout=15) as client:
        headers = _project_headers(_refresh_session(client, server))
        artifact_id = _resolve_artifact(
            client, server, headers, repository["project_id"], artifact
        )
        response = client.patch(
            f"{server}/api/v1/artifacts/{artifact_id}",
            headers=headers,
            json={"kind": kind, "description": description},
        )
        response.raise_for_status()
    value = response.json()
    typer.echo(f"{value['id']}\t{value['kind']}\t{value['name']}")


def _artifact_alias_context(reference: str) -> tuple[str, str, dict[str, str]]:
    repository = _repository_context()
    server = repository["server"].rstrip("/")
    with httpx.Client(timeout=15) as client:
        headers = _project_headers(_refresh_session(client, server))
        artifact_id = _resolve_artifact(
            client, server, headers, repository["project_id"], reference
        )
    return server, artifact_id, headers


@artifact_alias_app.command("list")
def artifact_alias_list(
    artifact: Annotated[str, typer.Argument(help="Artifact family ID or name")],
) -> None:
    """List mutable labels and their exact immutable targets."""
    server, artifact_id, headers = _artifact_alias_context(artifact)
    with httpx.Client(timeout=15) as client:
        response = client.get(
            f"{server}/api/v1/artifacts/{artifact_id}/aliases", headers=headers
        )
        response.raise_for_status()
    for value in response.json():
        typer.echo(f"{value['alias']}\t{value['artifact_version_id']}")


@artifact_alias_app.command("set")
def artifact_alias_set(
    artifact: Annotated[str, typer.Argument(help="Artifact family ID or name")],
    alias: Annotated[str, typer.Argument()],
    version: Annotated[str, typer.Argument(help="Exact Artifact Version ID")],
) -> None:
    """Create or atomically move an audited Artifact alias."""
    server, artifact_id, headers = _artifact_alias_context(artifact)
    with httpx.Client(timeout=15) as client:
        response = client.put(
            f"{server}/api/v1/artifacts/{artifact_id}/aliases/{alias}",
            headers=headers,
            json={"artifact_version_id": version},
        )
        response.raise_for_status()
    value = response.json()
    typer.echo(f"{value['alias']}\t{value['artifact_version_id']}")


@artifact_alias_app.command("delete")
def artifact_alias_delete(
    artifact: Annotated[str, typer.Argument(help="Artifact family ID or name")],
    alias: Annotated[str, typer.Argument()],
) -> None:
    """Delete an audited Artifact alias."""
    server, artifact_id, headers = _artifact_alias_context(artifact)
    with httpx.Client(timeout=15) as client:
        response = client.delete(
            f"{server}/api/v1/artifacts/{artifact_id}/aliases/{alias}", headers=headers
        )
        response.raise_for_status()
    typer.echo(f"Deleted alias {alias}.")


@publication_app.command("submit")
def submit_publication(
    artifact: Annotated[str, typer.Option("--artifact", help="Artifact family ID or name")],
    output: Annotated[str, typer.Option("--out")],
    pipeline: Annotated[str | None, typer.Option("--pipeline")] = None,
    stage: Annotated[str | None, typer.Option("--stage")] = None,
    dvc_file: Annotated[str | None, typer.Option("--dvc-file")] = None,
    run_id: Annotated[str | None, typer.Option("--run-id")] = None,
) -> None:
    """Submit committed DVC metadata for server-side publication validation."""
    repository = _repository_context()
    root = repository_root()
    commit_sha = _committed_upstream_state(root)
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
        platform_headers = _project_headers(session)
        artifact_id = _resolve_artifact(
            client, server, platform_headers, repository["project_id"], artifact
        )
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
    environment_kind: Annotated[str | None, typer.Option("--environment-kind")] = None,
    environment_name: Annotated[str | None, typer.Option("--environment-name")] = None,
    secrets_enabled: Annotated[
        bool | None, typer.Option("--secrets/--no-secrets")
    ] = None,
) -> None:
    """Run a local command with automatically captured, immutable provenance."""
    command = list(context.args)
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        raise typer.BadParameter("a command is required after --")
    repository = _repository_context()
    root = repository_root()
    server = repository["server"].rstrip("/")
    selection = resolve_runtime(
        root,
        command,
        kind_override=environment_kind,
        name_override=environment_name,
        secrets_override=secrets_enabled,
    )
    captured_before = capture_runtime(root, command, selection)
    if selection.secrets_enabled and shutil.which("infisical") is None:
        raise RuntimeError("Infisical CLI is required for this Run")
    commit_sha = _committed_upstream_state(root)
    experiment_refs_before = _dvc_experiment_refs(root)
    secret_context: dict[str, str] | None = None
    with httpx.Client(timeout=15) as client:
        session = _refresh_session(client, server)
        headers = _project_headers(session)
        environment_id = _resolve_environment(
            client,
            server,
            headers,
            repository["project_id"],
            selection.name,
            selection.kind,
            captured_before.document,
        )
        if selection.secrets_enabled:
            secret_response = client.get(
                f"{server}/api/v1/projects/{repository['project_id']}/secret-context",
                headers=headers,
            )
            secret_response.raise_for_status()
            configured_secret = secret_response.json()
            if configured_secret.get("reconciliation_state") != "in_sync":
                raise RuntimeError("project secret context is not reconciled")
            secret_context = {
                "project_id": str(configured_secret["infisical_project_id"]),
                "environment": str(configured_secret["environment_slug"]),
                "path": str(configured_secret["secret_path"]),
            }
        created = client.post(
            f"{server}/api/v1/projects/{repository['project_id']}/runs",
            headers=headers,
            json={
                "repository_id": repository["repository_id"],
                "experiment_name": experiment,
                "command": command,
                "pipeline_version_id": None,
                "environment_specification_id": environment_id,
            },
        )
        created.raise_for_status()
        run_record = created.json()
    run_id = run_record["id"]
    logging_token = run_record.get("logging_token")
    if not isinstance(logging_token, str):
        raise RuntimeError("platform did not return a Run-scoped logging token")
    typer.echo(f"Run {run_id} started.")

    token_descriptor, token_name = tempfile.mkstemp(prefix="homebrew-mlflow-token-")
    token_path = Path(token_name)
    with os.fdopen(token_descriptor, "w", encoding="utf-8") as token_file:
        token_file.write(logging_token)
        token_file.flush()
        os.fsync(token_file.fileno())

    stop = threading.Event()
    heartbeat_error: list[str] = []

    heartbeat_thread = threading.Thread(
        target=_send_run_heartbeats,
        args=(stop, server, run_id, token_path, heartbeat_error),
        daemon=True,
    )
    heartbeat_thread.start()
    child_environment = os.environ.copy()
    child_environment.update(
        {
            "HOMEBREW_MLFLOW_RUN_ID": run_id,
            "HOMEBREW_MLFLOW_PROJECT_ID": repository["project_id"],
            "HOMEBREW_MLFLOW_REPOSITORY_ID": repository["repository_id"],
            "HOMEBREW_MLFLOW_SERVER": server,
            "MLFLOW_TRACKING_URI": f"{server}/mlflow",
            "MLFLOW_TRACKING_AUTH": "homebrew-token-file",
            "MLFLOW_TRACKING_TOKEN_FILE": str(token_path),
            "MLFLOW_RUN_ID": run_id,
            "MLFLOW_EXPERIMENT_ID": str(run_record["experiment_id"]),
        }
    )
    interrupted = False
    finalization_token = logging_token
    child_command = list(captured_before.command)
    if selection.kind == "container":
        run_index = child_command.index("run")
        container_token_path = "/run/homebrew-mlflow/token"
        child_environment["MLFLOW_TRACKING_TOKEN_FILE"] = container_token_path
        inherited = [
            "HOMEBREW_MLFLOW_RUN_ID",
            "HOMEBREW_MLFLOW_PROJECT_ID",
            "HOMEBREW_MLFLOW_REPOSITORY_ID",
            "HOMEBREW_MLFLOW_SERVER",
            "MLFLOW_TRACKING_URI",
            "MLFLOW_TRACKING_AUTH",
            "MLFLOW_TRACKING_TOKEN_FILE",
            "MLFLOW_RUN_ID",
            "MLFLOW_EXPERIMENT_ID",
        ]
        child_command[run_index + 1 : run_index + 1] = [
            "--mount",
            f"type=bind,source={token_path},target={container_token_path},readonly",
            *(value for name in inherited for value in ("--env", name)),
        ]
    if secret_context is not None:
        child_command = [
            "infisical",
            "run",
            "--projectId",
            secret_context["project_id"],
            "--env",
            secret_context["environment"],
            "--path",
            secret_context["path"],
            "--",
            *child_command,
        ]
    try:
        completed = subprocess.run(child_command, check=False, env=child_environment)  # noqa: S603
        exit_code = completed.returncode
    except KeyboardInterrupt:
        interrupted = True
        exit_code = 130
    finally:
        stop.set()
        heartbeat_thread.join(timeout=20)
        if token_path.exists():
            finalization_token = token_path.read_text(encoding="utf-8").strip()

    provenance_problems: list[str] = []
    provenance_status = "complete"
    dvc_experiment_revision: str | None = None
    changed_experiment_refs: list[str] = []
    pipeline_version_id: str | None = None
    captured_after = None
    final_commit = ""
    try:
        final_commit = _git_output("rev-parse", "HEAD", root=root)
        if final_commit != commit_sha:
            provenance_problems.append("head_changed")
        captured_after = capture_runtime(root, command, selection)
        if captured_after.fingerprint != captured_before.fingerprint:
            provenance_problems.append("environment_drift")
    except RuntimeError as error:
        provenance_problems.append(str(error))

    try:
        experiment_refs_after = _dvc_experiment_refs(root)
        (
            dvc_experiment_revision,
            changed_experiment_refs,
            experiment_problems,
        ) = _changed_dvc_experiment(
            root, commit_sha, experiment_refs_before, experiment_refs_after
        )
        provenance_problems.extend(experiment_problems)
    except RuntimeError as error:
        provenance_problems.append(str(error))

    dvc_tracking_capture: dict[str, Any] = {
        "status": "skipped",
        "reason": "no_exact_experiment_revision",
    }
    try:
        if dvc_experiment_revision is not None:
            dvc_tracking_capture = _capture_dvc_tracking(
                root,
                selection.kind,
                selection.name,
                commit_sha,
                dvc_experiment_revision,
                server,
                run_id,
                finalization_token,
            )
            for warning in dvc_tracking_capture.get("warnings", []):
                typer.echo(f"Warning: {warning}", err=True)
    except (RuntimeError, OSError, ValueError, json.JSONDecodeError, httpx.HTTPError) as error:
        dvc_tracking_capture = {
            "status": "warning",
            "error": _safe_error(error),
        }
        typer.echo(
            f"Warning: automatic DVC tracking capture was skipped ({_safe_error(error)}).",
            err=True,
        )
    finally:
        token_path.unlink(missing_ok=True)

    try:
        workspace_status = _git_output("status", "--porcelain", root=root).splitlines()
    except RuntimeError as error:
        workspace_status = []
        provenance_problems.append(str(error))
    if provenance_problems:
        provenance_status = "invalid"
    elif dvc_experiment_revision is None and workspace_status:
        provenance_status = "incomplete"
    status_value = "interrupted" if interrupted else ("succeeded" if exit_code == 0 else "failed")
    pipeline_resolution = (
        {
            "repository_id": repository["repository_id"],
            "git_commit_sha": commit_sha,
            "pipeline_path": "dvc.yaml",
        }
        if final_commit == commit_sha and (root / "dvc.yaml").is_file()
        else None
    )
    finalization = {
                "exit_code": exit_code,
                "status": status_value,
                "git_commit_sha": commit_sha,
                "provenance_status": provenance_status,
                "dvc_experiment_revision": dvc_experiment_revision,
                "pipeline_version_id": pipeline_version_id,
                "environment_specification_id": environment_id,
                "evidence": {
                    "heartbeat_error": heartbeat_error[-1] if heartbeat_error else None,
                    "heartbeat_error_count": len(heartbeat_error),
                    "client_version": __version__,
                    "input_artifact_version_ids": input_version or [],
                    "dvc_tracking_capture": dvc_tracking_capture,
                    "secret_context": secret_context,
                    "environment": {
                        "kind": selection.kind,
                        "name": selection.name,
                        "fingerprint_before": captured_before.fingerprint,
                        "fingerprint_after": (
                            captured_after.fingerprint if captured_after is not None else None
                        ),
                    },
                    "provenance_error": provenance_problems[0] if provenance_problems else None,
                    "provenance": {
                        "schema": 1,
                        "status": provenance_status,
                        "base_git_commit_sha": commit_sha,
                        "dvc_experiment_revision": dvc_experiment_revision,
                        "changed_experiment_refs": changed_experiment_refs,
                        "workspace_changes": workspace_status[:200],
                        "dvc_metadata": (
                            _git_blob_evidence(root, dvc_experiment_revision, "dvc.lock")
                            if dvc_experiment_revision is not None
                            else None
                        ),
                        "problems": provenance_problems,
                    },
                },
            }
    journal = {
        "schema": 1,
        "server": server,
        "run_id": run_id,
        "project_id": repository["project_id"],
        "repository_id": repository["repository_id"],
        "idempotency_key": secrets.token_urlsafe(24),
        "pipeline_resolution": pipeline_resolution,
        "finalization": finalization,
    }
    journal_path = _finalization_journal_path(server, run_id)
    _write_private_json(journal_path, journal)
    try:
        _recover_finalization(journal_path, run_token=finalization_token)
    except Exception as error:
        typer.echo(f"Run {run_id} could not be finalized: {error}", err=True)
        typer.echo(f"Recover it with: homebrew-mlflow run-recover {run_id}", err=True)
        raise typer.Exit(exit_code if exit_code else 1) from error
    typer.echo(f"Run {run_id} finalized as {status_value}.")
    if provenance_status != "complete":
        typer.echo(
            f"Warning: Run provenance is {provenance_status}; "
            "it cannot be used for archival publication.",
            err=True,
        )
    if exit_code:
        raise typer.Exit(exit_code)


@app.command("run-recover")
def run_recover(run_id: Annotated[str, typer.Argument()]) -> None:
    """Retry a locally journaled Run finalization without rerunning computation."""
    path = _find_finalization_journal(run_id)
    try:
        result = _recover_finalization(path)
    except Exception as error:
        typer.echo(f"Run {run_id} could not be recovered: {error}", err=True)
        raise typer.Exit(1) from error
    typer.echo(f"Run {run_id} finalized as {result.get('state', 'terminal')}.")


if __name__ == "__main__":
    app()
