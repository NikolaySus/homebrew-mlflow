from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

RuntimeKind = Literal["uv", "pip", "conda", "container", "system"]
_KINDS = {"uv", "pip", "conda", "container", "system"}


@dataclass(frozen=True)
class RuntimeSelection:
    kind: RuntimeKind
    name: str
    secrets_enabled: bool


@dataclass(frozen=True)
class RuntimeCapture:
    selection: RuntimeSelection
    document: dict[str, Any]
    fingerprint: str
    command: tuple[str, ...]


def load_repository_config(root: Path) -> dict[str, Any]:
    path = root / "homebrew-mlflow.toml"
    if not path.is_file():
        return {}
    try:
        value = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise RuntimeError(f"invalid {path.name}: {error}") from error
    return value


def resolve_runtime(
    root: Path,
    command: list[str],
    *,
    kind_override: str | None = None,
    name_override: str | None = None,
    secrets_override: bool | None = None,
) -> RuntimeSelection:
    config = load_repository_config(root)
    environment = config.get("environment", {})
    secrets = config.get("secrets", {})
    configured_kind = environment.get("kind") if isinstance(environment, dict) else None
    configured_name = environment.get("name") if isinstance(environment, dict) else None
    kind = kind_override or configured_kind
    if kind is None:
        candidates = _detect_runtime_candidates(root, command)
        if len(candidates) != 1:
            found = ", ".join(sorted(candidates)) or "none"
            raise RuntimeError(
                "runtime detection must produce exactly one result "
                f"(found: {found}); set [environment].kind in homebrew-mlflow.toml"
            )
        kind = next(iter(candidates))
    if kind not in _KINDS:
        raise RuntimeError(f"unsupported environment kind: {kind}")
    name = name_override or configured_name or "default"
    if not isinstance(name, str) or not name.strip():
        raise RuntimeError("environment name must be a non-empty string")
    enabled = secrets_override
    if enabled is None:
        enabled = bool(secrets.get("enabled", False)) if isinstance(secrets, dict) else False
    return RuntimeSelection(cast(RuntimeKind, kind), name.strip(), enabled)


def capture_runtime(
    root: Path,
    command: list[str],
    selection: RuntimeSelection,
) -> RuntimeCapture:
    if selection.kind == "uv":
        document, child = _capture_uv(root, command)
    elif selection.kind == "pip":
        document, child = _capture_pip(root, command)
    elif selection.kind == "conda":
        document, child = _capture_conda(root, command, selection.name)
    elif selection.kind == "container":
        document, child = _capture_container(root, command)
    else:
        document, child = _capture_system(root, command)
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"))
    return RuntimeCapture(
        selection=selection,
        document=document,
        fingerprint=hashlib.sha256(canonical.encode()).hexdigest(),
        command=tuple(child),
    )


def _detect_runtime_candidates(root: Path, command: list[str]) -> set[RuntimeKind]:
    candidates: set[RuntimeKind] = set()
    executable = Path(command[0]).name.lower() if command else ""
    if executable in {"docker", "docker.exe"}:
        candidates.add("container")
    if (root / "uv.lock").is_file():
        candidates.add("uv")
    if any((root / name).is_file() for name in ("requirements.txt", "requirements.lock")):
        candidates.add("pip")
    if any((root / name).is_file() for name in ("environment.yml", "environment.yaml")):
        candidates.add("conda")
    if not candidates:
        candidates.add("system")
    return candidates


def _run_json(command: list[str], root: Path) -> Any:
    result = subprocess.run(
        command,
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip().splitlines()[-1:] or ["unknown error"]
        raise RuntimeError(f"runtime preflight failed: {' '.join(command[:3])}: {detail[0]}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError("runtime preflight returned invalid JSON") from error


def _python_probe() -> str:
    return (
        "import importlib.metadata as m,json,platform,sys;"
        "print(json.dumps({'python':platform.python_version(),"
        "'implementation':platform.python_implementation(),"
        "'packages':sorted((d.metadata.get('Name','').lower(),d.version) "
        "for d in m.distributions() if d.metadata.get('Name'))}))"
    )


def _file_hashes(root: Path, names: tuple[str, ...]) -> dict[str, str]:
    values: dict[str, str] = {}
    for name in names:
        path = root / name
        if path.is_file():
            values[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return values


def _tool_version(command: list[str], root: Path) -> str:
    result = subprocess.run(command, cwd=root, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"required runtime tool is unavailable: {command[0]}")
    return result.stdout.strip().splitlines()[0]


def _capture_uv(root: Path, command: list[str]) -> tuple[dict[str, Any], list[str]]:
    if shutil.which("uv") is None:
        raise RuntimeError("uv is required by the selected environment")
    if not (root / "uv.lock").is_file():
        raise RuntimeError("the uv environment requires a committed uv.lock")
    probe = _run_json(["uv", "run", "--frozen", "python", "-c", _python_probe()], root)
    document = {
        "schema": 1,
        "kind": "uv",
        "tool": _tool_version(["uv", "--version"], root),
        "python": probe["python"],
        "implementation": probe["implementation"],
        "packages": probe["packages"],
        "files": _file_hashes(root, ("pyproject.toml", "uv.lock", ".python-version")),
    }
    return document, ["uv", "run", "--frozen", "--", *command]


def _capture_pip(root: Path, command: list[str]) -> tuple[dict[str, Any], list[str]]:
    interpreter = _venv_python(root) or sys.executable
    probe = _run_json([interpreter, "-c", _python_probe()], root)
    document = {
        "schema": 1,
        "kind": "pip",
        "python": probe["python"],
        "implementation": probe["implementation"],
        "packages": probe["packages"],
        "files": _file_hashes(
            root, ("pyproject.toml", "requirements.txt", "requirements.lock", "Pipfile.lock")
        ),
    }
    child = (
        [interpreter, *command[1:]]
        if Path(command[0]).name in {"python", "python.exe"}
        else command
    )
    return document, child


def _capture_conda(
    root: Path, command: list[str], environment_name: str
) -> tuple[dict[str, Any], list[str]]:
    if shutil.which("conda") is None:
        raise RuntimeError("conda is required by the selected environment")
    probe = _run_json(
        ["conda", "run", "-n", environment_name, "python", "-c", _python_probe()], root
    )
    explicit = subprocess.run(
        ["conda", "list", "-n", environment_name, "--explicit"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if explicit.returncode != 0:
        raise RuntimeError(f"conda environment does not exist: {environment_name}")
    packages = sorted(
        line.strip() for line in explicit.stdout.splitlines() if line and not line.startswith("#")
    )
    document = {
        "schema": 1,
        "kind": "conda",
        "tool": _tool_version(["conda", "--version"], root),
        "python": probe["python"],
        "implementation": probe["implementation"],
        "packages": packages,
        "files": _file_hashes(root, ("environment.yml", "environment.yaml", "conda-lock.yml")),
    }
    return document, ["conda", "run", "-n", environment_name, "--no-capture-output", *command]


def _capture_container(root: Path, command: list[str]) -> tuple[dict[str, Any], list[str]]:
    if shutil.which("docker") is None:
        raise RuntimeError("Docker is required by the selected container environment")
    if Path(command[0]).name.lower() not in {"docker", "docker.exe"} or "run" not in command:
        raise RuntimeError("container runs must use an explicit `docker run` command")
    run_index = command.index("run")
    image_index = _docker_image_index(command, run_index + 1)
    image = command[image_index]
    digests = _run_json(
        ["docker", "image", "inspect", image, "--format", "{{json .RepoDigests}}"], root
    )
    if not isinstance(digests, list) or not digests:
        raise RuntimeError("container image has no resolved repository digest")
    child = [*command[: run_index + 1], "--pull=never", *command[run_index + 1 :]]
    document = {
        "schema": 1,
        "kind": "container",
        "engine": _tool_version(["docker", "--version"], root),
        "image": image,
        "digests": sorted(str(value) for value in digests),
        "platform": {"os": platform.system().lower(), "architecture": platform.machine().lower()},
    }
    return document, child


def _docker_image_index(command: list[str], start: int) -> int:
    options_with_value = {
        "--add-host", "--env", "-e", "--env-file", "--mount", "--name", "--network",
        "--platform", "--publish", "-p", "--user", "-u", "--volume", "-v", "--workdir", "-w",
    }
    index = start
    while index < len(command):
        value = command[index]
        if value in options_with_value:
            index += 2
        elif value.startswith("-"):
            index += 1
        else:
            return index
    raise RuntimeError("docker run command does not contain an image")


def _capture_system(root: Path, command: list[str]) -> tuple[dict[str, Any], list[str]]:
    executable = shutil.which(command[0])
    if executable is None:
        raise RuntimeError(f"command executable was not found: {command[0]}")
    path = Path(executable)
    document = {
        "schema": 1,
        "kind": "system",
        "platform": {
            "os": platform.system().lower(),
            "release": platform.release(),
            "architecture": platform.machine().lower(),
        },
        "executable": {
            "name": path.name,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        },
    }
    return document, command


def _venv_python(root: Path) -> str | None:
    candidates = (root / ".venv" / "Scripts" / "python.exe", root / ".venv" / "bin" / "python")
    return next((str(path) for path in candidates if path.is_file()), None)


def installed_cli_version() -> str:
    return importlib.metadata.version("homebrew-mlflow")
