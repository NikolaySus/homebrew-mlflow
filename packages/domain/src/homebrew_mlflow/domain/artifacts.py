from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from .identifiers import PublicId, ResourceKind


class OutputKind(StrEnum):
    FILE = "file"
    DIRECTORY = "directory"


class IntegrityState(StrEnum):
    VERIFIED = "verified"
    CORRUPT = "corrupt"
    UNKNOWN = "unknown"


class AvailabilityState(StrEnum):
    AVAILABLE = "available"
    MISSING = "missing"
    PURGED = "purged"


@dataclass(frozen=True, slots=True)
class Artifact:
    id: PublicId
    owning_project_id: PublicId
    name: str
    created_at: datetime
    archived_at: datetime | None = None

    @classmethod
    def create(cls, project_id: PublicId, name: str, created_at: datetime) -> Artifact:
        if project_id.kind is not ResourceKind.PROJECT:
            raise ValueError("Artifact must belong to a Research Project")
        normalized = name.strip()
        if not normalized or len(normalized) > 200:
            raise ValueError("Artifact name must contain between 1 and 200 characters")
        return cls(PublicId.generate(ResourceKind.ARTIFACT), project_id, normalized, created_at)


@dataclass(frozen=True, slots=True)
class DvcOutputIdentity:
    algorithm: str
    digest: str
    kind: OutputKind
    size: int
    file_count: int

    def __post_init__(self) -> None:
        if self.algorithm not in {"md5", "sha256"}:
            raise ValueError("unsupported DVC hash algorithm")
        expected_length = 32 if self.algorithm == "md5" else 64
        if len(self.digest) != expected_length or any(
            character not in "0123456789abcdef" for character in self.digest
        ):
            raise ValueError("invalid DVC digest")
        if self.size < 0 or self.file_count < 1:
            raise ValueError("artifact size and file count must be valid")
        if self.kind is OutputKind.FILE and self.file_count != 1:
            raise ValueError("file output must have exactly one file")


@dataclass(frozen=True, slots=True)
class ArtifactVersion:
    id: PublicId
    artifact_id: PublicId
    owning_project_id: PublicId
    identity: DvcOutputIdentity
    integrity: IntegrityState
    availability: AvailabilityState
    published_at: datetime
    archived_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.id.kind is not ResourceKind.ARTIFACT_VERSION:
            raise ValueError("invalid Artifact Version identifier")
        if self.artifact_id.kind is not ResourceKind.ARTIFACT:
            raise ValueError("invalid Artifact identifier")
        if self.owning_project_id.kind is not ResourceKind.PROJECT:
            raise ValueError("invalid owning project identifier")
