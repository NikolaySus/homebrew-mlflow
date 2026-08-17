from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath

from .identifiers import PublicId, ResourceKind


@dataclass(frozen=True, slots=True)
class PipelineDefinition:
    id: PublicId
    project_id: PublicId
    name: str
    created_at: datetime
    archived_at: datetime | None = None

    @classmethod
    def create(cls, project_id: PublicId, name: str, now: datetime) -> PipelineDefinition:
        normalized = name.strip()
        if project_id.kind is not ResourceKind.PROJECT:
            raise ValueError("Pipeline Definition must belong to a Research Project")
        if not normalized or len(normalized) > 200:
            raise ValueError("Pipeline Definition name must contain 1 to 200 characters")
        return cls(PublicId.generate(ResourceKind.PIPELINE_DEFINITION), project_id, normalized, now)


@dataclass(frozen=True, slots=True)
class PipelineVersion:
    id: PublicId
    definition_id: PublicId
    repository_id: PublicId
    git_commit_sha: str
    pipeline_path: str
    content_sha256: str
    created_at: datetime
    archived_at: datetime | None = None

    @classmethod
    def create(
        cls,
        definition_id: PublicId,
        repository_id: PublicId,
        git_commit_sha: str,
        pipeline_path: str,
        content_sha256: str,
        now: datetime,
    ) -> PipelineVersion:
        path = PurePosixPath(pipeline_path)
        if definition_id.kind is not ResourceKind.PIPELINE_DEFINITION:
            raise ValueError("invalid Pipeline Definition identifier")
        if repository_id.kind is not ResourceKind.REPOSITORY:
            raise ValueError("invalid Repository identifier")
        if len(git_commit_sha) != 40 or any(c not in "0123456789abcdef" for c in git_commit_sha):
            raise ValueError("Git commit SHA must be a full lowercase SHA-1")
        if (
            not pipeline_path
            or path.is_absolute()
            or ".." in path.parts
            or "\\" in pipeline_path
        ):
            raise ValueError("pipeline path must be a safe repository-relative POSIX path")
        if len(content_sha256) != 64 or any(
            c not in "0123456789abcdef" for c in content_sha256
        ):
            raise ValueError("pipeline content SHA-256 is invalid")
        return cls(
            PublicId.generate(ResourceKind.PIPELINE_VERSION),
            definition_id,
            repository_id,
            git_commit_sha,
            pipeline_path,
            content_sha256,
            now,
        )
