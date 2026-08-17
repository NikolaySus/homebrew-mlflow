from __future__ import annotations

import re
from dataclasses import fields
from pathlib import Path

from homebrew_mlflow.application import (
    RepositorySeedFile,
    RepositoryTemplateContext,
)

_PLACEHOLDER = re.compile(r"{{\s*([a-z][a-z0-9_]*)\s*}}")


class RepositoryTemplateError(ValueError):
    pass


class FileSystemRepositoryTemplate:
    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def render(self, context: RepositoryTemplateContext) -> tuple[RepositorySeedFile, ...]:
        if not self._root.is_dir():
            raise RepositoryTemplateError(f"repository template does not exist: {self._root}")
        values = {field.name: str(getattr(context, field.name)) for field in fields(context)}
        if any("\n" in value or "\r" in value for value in values.values()):
            raise RepositoryTemplateError("template values must not contain line breaks")
        rendered: list[RepositorySeedFile] = []
        for source in sorted(self._root.rglob("*")):
            if source.is_symlink():
                raise RepositoryTemplateError(f"template symlinks are not allowed: {source}")
            if source.is_dir():
                continue
            relative = source.relative_to(self._root).as_posix()
            try:
                content = source.read_text(encoding="utf-8")
            except UnicodeDecodeError as error:
                raise RepositoryTemplateError(
                    f"template files must be UTF-8 text: {relative}"
                ) from error

            unknown = sorted(set(_PLACEHOLDER.findall(content)) - values.keys())
            if unknown:
                names = ", ".join(unknown)
                raise RepositoryTemplateError(f"unknown placeholders in {relative}: {names}")
            content = _PLACEHOLDER.sub(lambda match: values[match.group(1)], content)
            rendered.append(
                RepositorySeedFile(
                    path=relative,
                    content=content,
                    executable=relative.startswith("scripts/")
                    and (relative.endswith(".sh") or relative.endswith(".py")),
                )
            )
        return tuple(rendered)
