from __future__ import annotations

import unicodedata
from pathlib import PurePosixPath


class UnsafeArtifactPath(ValueError):
    pass


def normalize_artifact_path(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value)
    if not normalized or "\\" in normalized or "\x00" in normalized:
        raise UnsafeArtifactPath("artifact path is empty or contains forbidden characters")
    path = PurePosixPath(normalized)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise UnsafeArtifactPath("artifact path must be a normalized relative path")
    return path.as_posix()


def normalize_file_index(paths: list[str]) -> tuple[str, ...]:
    normalized = tuple(normalize_artifact_path(path) for path in paths)
    if len(set(normalized)) != len(normalized):
        raise UnsafeArtifactPath("file index contains duplicate normalized paths")
    return normalized
