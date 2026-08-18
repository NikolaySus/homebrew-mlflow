from __future__ import annotations

import configparser
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class DvcConfiguration:
    remote_name: str
    remote_url: str
    endpoint_url: str
    profile: str
    credential_process: str

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> DvcConfiguration:
        required = (
            "remote_name",
            "remote_url",
            "endpoint_url",
            "profile",
            "credential_process",
        )
        if not all(isinstance(payload.get(key), str) and payload[key] for key in required):
            raise RuntimeError("platform returned an invalid DVC configuration")
        return cls(*(str(payload[key]) for key in required))


@dataclass(frozen=True, slots=True)
class RepositoryTemplateUpgrade:
    root: Path
    source_version: int
    target_version: int
    content_by_path: dict[Path, str]

    def apply(self) -> tuple[Path, ...]:
        changed: list[Path] = []
        for path, content in self.content_by_path.items():
            if path.is_file() and path.read_text(encoding="utf-8") == content:
                continue
            _write_atomic(path, content)
            changed.append(path)
        return tuple(changed)


_LATEST_TEMPLATE_VERSION = 5


def prepare_repository_template_upgrade(root: Path) -> RepositoryTemplateUpgrade:
    sentinel_path = root / ".homebrew-mlflow.json"
    try:
        sentinel = json.loads(sentinel_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError("invalid .homebrew-mlflow.json repository context") from error
    version = sentinel.get("template_version")
    if not isinstance(version, int) or isinstance(version, bool):
        raise RuntimeError("repository template version is missing or invalid")
    if version > _LATEST_TEMPLATE_VERSION:
        raise RuntimeError(
            "repository template is newer than this CLI; update homebrew-mlflow first"
        )
    if version < 2:
        raise RuntimeError(f"unsupported repository template version: {version}")

    content_by_path: dict[Path, str] = {}
    current = version
    while current < _LATEST_TEMPLATE_VERSION:
        if current == 2:
            _prepare_v2_to_v3(root, content_by_path)
        elif current == 3:
            _prepare_v3_to_v4(root, content_by_path)
        elif current == 4:
            server = sentinel.get("server")
            if not isinstance(server, str) or not server.startswith(("http://", "https://")):
                raise RuntimeError("repository server URL is missing or invalid")
            _prepare_v4_to_v5(root, content_by_path, server.rstrip("/"))
        current += 1
    sentinel["template_version"] = current
    content_by_path[sentinel_path] = json.dumps(sentinel, indent=2, ensure_ascii=False) + "\n"
    return RepositoryTemplateUpgrade(root, version, current, content_by_path)


def _pending_content(root: Path, changes: dict[Path, str], relative: str) -> tuple[Path, str]:
    path = root / relative
    if path in changes:
        return path, changes[path]
    try:
        return path, path.read_text(encoding="utf-8")
    except OSError as error:
        raise RuntimeError(f"repository template migration requires {relative}") from error


def _replace_managed_fragment(content: str, old: str, new: str, relative: str) -> str:
    if new in content:
        return content
    if old not in content:
        raise RuntimeError(
            f"repository template migration conflicts with researcher changes in {relative}"
        )
    return content.replace(old, new, 1)


def _prepare_v2_to_v3(root: Path, changes: dict[Path, str]) -> None:
    readme_path, readme = _pending_content(root, changes, "README.md")
    readme = _replace_managed_fragment(
        readme,
        "dvc status\nhomebrew-mlflow run --experiment <name> -- dvc exp run -n <experiment-name>\n"
        "dvc metrics show",
        "uv run --frozen dvc status\n"
        "homebrew-mlflow run --experiment <name> -- dvc exp run -n <experiment-name>\n"
        "uv run --frozen dvc metrics show",
        "README.md",
    )
    readme = _replace_managed_fragment(
        readme,
        "`dvc push -r platform` transfers",
        "`uv run --frozen dvc push -r platform` transfers",
        "README.md",
    )
    changes[readme_path] = readme

    agents_path, agents = _pending_content(root, changes, "AGENTS.md")
    agents = _replace_managed_fragment(
        agents,
        "dvc status\ndvc push -r platform",
        "uv run --frozen dvc status\nuv run --frozen dvc push -r platform",
        "AGENTS.md",
    )
    changes[agents_path] = agents


def _prepare_v3_to_v4(root: Path, changes: dict[Path, str]) -> None:
    agents_path, agents = _pending_content(root, changes, "AGENTS.md")
    agents = _replace_managed_fragment(
        agents,
        "recent commits, `dvc status`, and `dvc dag` before changing",
        "recent commits, `uv run --frozen dvc status`, and\n"
        "   `uv run --frozen dvc dag` before changing",
        "AGENTS.md",
    )
    explanation = (
        "The Run helper executes the child through the declared uv environment; "
        "do not add a nested "
        "`uv run`\ninside `homebrew-mlflow run`."
    )
    agents = _replace_managed_fragment(
        agents,
        "homebrew-mlflow run --experiment <name> -- dvc exp run -n <name>\n```",
        "homebrew-mlflow run --experiment <name> -- dvc exp run -n <name>\n```\n\n"
        + explanation,
        "AGENTS.md",
    )
    changes[agents_path] = agents

    readme_path, readme = _pending_content(root, changes, "README.md")
    readme_explanation = (
        "The Run helper executes its child through the selected uv environment, "
        "so the command after `--` "
        "stays\n`dvc ...` rather than nesting another `uv run`."
    )
    readme = _replace_managed_fragment(
        readme,
        "uv run --frozen dvc metrics show\n```",
        "uv run --frozen dvc metrics show\n```\n\n" + readme_explanation,
        "README.md",
    )
    changes[readme_path] = readme


def _prepare_v4_to_v5(
    root: Path, changes: dict[Path, str], platform_url: str
) -> None:
    pyproject_path, pyproject = _pending_content(root, changes, "pyproject.toml")
    pyproject = _replace_managed_fragment(
        pyproject,
        '"homebrew-mlflow-plugins==0.1.0"',
        '"homebrew-mlflow-plugins==0.1.1"',
        "pyproject.toml",
    )
    changes[pyproject_path] = pyproject

    lock_path, lock = _pending_content(root, changes, "uv.lock")
    old_wheel = (
        f'    {{ url = "{platform_url}/packages/files/'
        'homebrew_mlflow_plugins-0.1.0-py3-none-any.whl", '
        'hash = "sha256:efe3e890c1fe7002552f2611443aadf504e1d569a6d4888d6f193004147bcadd" },'
    )
    old_package = f'''[[package]]
name = "homebrew-mlflow-plugins"
version = "0.1.0"
source = {{ registry = "{platform_url}/packages/simple/" }}
dependencies = [
    {{ name = "mlflow" }},
    {{ name = "requests" }},
]
wheels = [
{old_wheel}
]
'''
    new_package = old_package.replace('version = "0.1.0"', 'version = "0.1.1"', 1)
    new_package = new_package.replace(
        "homebrew_mlflow_plugins-0.1.0-py3-none-any.whl",
        "homebrew_mlflow_plugins-0.1.1-py3-none-any.whl",
    )
    new_package = new_package.replace(
        "efe3e890c1fe7002552f2611443aadf504e1d569a6d4888d6f193004147bcadd",
        "35aa43e74f96c4f5c24439decc7af59392c1777ef842458041827c2c1204c370",
    )
    lock = _replace_managed_fragment(lock, old_package, new_package, "uv.lock")
    old_requirement = (
        '{ name = "homebrew-mlflow-plugins", specifier = "==0.1.0", '
        f'index = "{platform_url}/packages/simple/" }}'
    )
    lock = _replace_managed_fragment(
        lock,
        old_requirement,
        old_requirement.replace("==0.1.0", "==0.1.1"),
        "uv.lock",
    )
    changes[lock_path] = lock


def read_repository_dvc_configuration(
    root: Path, remote_name: str = "platform"
) -> DvcConfiguration:
    dvc = _parser()
    dvc_path = root / ".dvc" / "config"
    aws_path = root / ".aws" / "config"
    if not dvc_path.is_file() or not aws_path.is_file():
        raise RuntimeError("repository is missing generated DVC or AWS configuration")
    dvc.read(dvc_path, encoding="utf-8")
    remote_section = f"'remote \"{remote_name}\"'"
    try:
        remote_url = dvc.get(remote_section, "url")
        endpoint_url = dvc.get(remote_section, "endpointurl")
        profile = dvc.get(remote_section, "profile")
    except (configparser.NoOptionError, configparser.NoSectionError) as error:
        raise RuntimeError(f"repository DVC remote {remote_name!r} is incomplete") from error
    aws = _parser()
    aws.read(aws_path, encoding="utf-8")
    try:
        credential_process = aws.get(f"profile {profile}", "credential_process")
    except (configparser.NoOptionError, configparser.NoSectionError) as error:
        raise RuntimeError(f"repository AWS profile {profile!r} is incomplete") from error
    return DvcConfiguration(
        remote_name, remote_url, endpoint_url, profile, credential_process
    )


def repository_root(start: Path | None = None) -> Path:
    current = (start or Path.cwd()).resolve()
    for directory in (current, *current.parents):
        if (directory / ".homebrew-mlflow.json").is_file():
            return directory
    raise RuntimeError("not inside a Homebrew MLflow research repository")


def install_dvc_profile(root: Path, *, aws_config: Path | None = None) -> tuple[str, Path]:
    source = root / ".aws" / "config"
    if not source.is_file():
        raise RuntimeError("repository does not contain its generated .aws/config profile")
    incoming = _parser()
    incoming.read(source, encoding="utf-8")
    sections = [
        section
        for section in incoming.sections()
        if section.startswith("profile homebrew-mlflow-")
    ]
    if len(sections) != 1:
        raise RuntimeError(
            "repository AWS configuration must contain exactly one Homebrew MLflow profile"
        )
    section = sections[0]
    target = aws_config or _aws_config_path()
    merged = _parser()
    if target.is_file():
        merged.read(target, encoding="utf-8")
    if not merged.has_section(section):
        merged.add_section(section)
    for key, value in incoming.items(section):
        merged.set(section, key, value)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix="config.", dir=target.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as temporary:
            merged.write(temporary)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, target)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)
    return section.removeprefix("profile "), target


def reconcile_repository_configuration(
    root: Path,
    configuration: DvcConfiguration,
    *,
    aws_config: Path | None = None,
) -> tuple[str, Path, tuple[Path, ...]]:
    changed: list[Path] = []
    dvc_path = root / ".dvc" / "config"
    dvc = _parser()
    if dvc_path.is_file():
        dvc.read(dvc_path, encoding="utf-8")
    if not dvc.has_section("core"):
        dvc.add_section("core")
    dvc.set("core", "remote", configuration.remote_name)
    remote_section = f"'remote \"{configuration.remote_name}\"'"
    if not dvc.has_section(remote_section):
        dvc.add_section(remote_section)
    for key, value in (
        ("url", configuration.remote_url),
        ("endpointurl", configuration.endpoint_url),
        ("profile", configuration.profile),
    ):
        dvc.set(remote_section, key, value)
    if _write_parser_if_changed(dvc_path, dvc):
        changed.append(dvc_path)

    source_path = root / ".aws" / "config"
    source = _parser()
    if source_path.is_file():
        source.read(source_path, encoding="utf-8")
    profile_section = f"profile {configuration.profile}"
    if not source.has_section(profile_section):
        source.add_section(profile_section)
    source.set(profile_section, "region", "us-east-1")
    source.set(profile_section, "credential_process", configuration.credential_process)
    if _write_parser_if_changed(source_path, source):
        changed.append(source_path)

    ignore_path = root / ".dvc" / ".gitignore"
    existing_lines = (
        ignore_path.read_text(encoding="utf-8").splitlines() if ignore_path.is_file() else []
    )
    ignore_lines = [*existing_lines]
    for line in ("/tmp", "/cache"):
        if line not in ignore_lines:
            ignore_lines.append(line)
    ignore_content = "\n".join(ignore_lines) + "\n"
    if not ignore_path.is_file() or ignore_path.read_text(encoding="utf-8") != ignore_content:
        _write_atomic(ignore_path, ignore_content)
        changed.append(ignore_path)

    profile, target = install_dvc_profile(root, aws_config=aws_config)
    return profile, target, tuple(changed)


def _aws_config_path() -> Path:
    configured = os.getenv("AWS_CONFIG_FILE")
    return Path(configured).expanduser() if configured else Path.home() / ".aws" / "config"


def _parser() -> configparser.RawConfigParser:
    return _CaseSensitiveConfigParser(interpolation=None)


class _CaseSensitiveConfigParser(configparser.RawConfigParser):
    def optionxform(self, optionstr: str) -> str:
        return optionstr


def _write_parser_if_changed(path: Path, parser: configparser.RawConfigParser) -> bool:
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8", newline="\n") as output:
        parser.write(output)
        output.seek(0)
        content = output.read()
    if path.is_file() and path.read_text(encoding="utf-8") == content:
        return False
    _write_atomic(path, content)
    return True


def _write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f"{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as temporary:
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)
