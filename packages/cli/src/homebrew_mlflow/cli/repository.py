from __future__ import annotations

import configparser
import os
import tempfile
from pathlib import Path


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
    sections = incoming.sections()
    if len(sections) != 1 or not sections[0].startswith("profile "):
        raise RuntimeError("repository AWS configuration must contain exactly one named profile")
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


def _aws_config_path() -> Path:
    configured = os.getenv("AWS_CONFIG_FILE")
    return Path(configured).expanduser() if configured else Path.home() / ".aws" / "config"


def _parser() -> configparser.RawConfigParser:
    return _CaseSensitiveConfigParser(interpolation=None)


class _CaseSensitiveConfigParser(configparser.RawConfigParser):
    def optionxform(self, optionstr: str) -> str:
        return optionstr
