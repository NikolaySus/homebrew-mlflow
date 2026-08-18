from __future__ import annotations

import configparser
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
