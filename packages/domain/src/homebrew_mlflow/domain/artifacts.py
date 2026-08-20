from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from .identifiers import PublicId, ResourceKind


class OutputKind(StrEnum):
    FILE = "file"
    DIRECTORY = "directory"


class ArtifactKind(StrEnum):
    DATASET = "dataset"
    MODEL = "model"
    CHECKPOINT = "checkpoint"
    REPORT = "report"
    GENERIC = "generic"


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
    kind: ArtifactKind = ArtifactKind.GENERIC
    description: str | None = None

    @classmethod
    def create(
        cls,
        project_id: PublicId,
        name: str,
        created_at: datetime,
        kind: ArtifactKind = ArtifactKind.GENERIC,
        description: str | None = None,
    ) -> Artifact:
        if project_id.kind is not ResourceKind.PROJECT:
            raise ValueError("Artifact must belong to a Research Project")
        normalized = name.strip()
        if not normalized or len(normalized) > 200:
            raise ValueError("Artifact name must contain between 1 and 200 characters")
        normalized_description = description.strip() if description is not None else None
        if normalized_description == "":
            normalized_description = None
        if normalized_description is not None and len(normalized_description) > 2000:
            raise ValueError("Artifact description cannot exceed 2000 characters")
        return cls(
            PublicId.generate(ResourceKind.ARTIFACT),
            project_id,
            normalized,
            created_at,
            kind=kind,
            description=normalized_description,
        )


_ALIAS_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
_VERSION_ALIAS_PATTERN = re.compile(r"^[vV]\d+$")


def normalize_artifact_alias(value: str) -> str:
    alias = value.strip()
    if not alias or len(alias) > 255 or _ALIAS_PATTERN.fullmatch(alias) is None:
        raise ValueError(
            "Artifact alias must contain 1-255 alphanumeric, underscore, or dash characters"
        )
    if alias.casefold() == "latest" or _VERSION_ALIAS_PATTERN.fullmatch(alias):
        raise ValueError("Artifact alias is reserved")
    return alias


@dataclass(frozen=True, slots=True)
class ArtifactAlias:
    artifact_id: PublicId
    alias: str
    artifact_version_id: PublicId
    created_by: PublicId
    created_at: datetime
    updated_by: PublicId
    updated_at: datetime

    def __post_init__(self) -> None:
        if self.artifact_id.kind is not ResourceKind.ARTIFACT:
            raise ValueError("invalid Artifact identifier")
        if self.artifact_version_id.kind is not ResourceKind.ARTIFACT_VERSION:
            raise ValueError("invalid Artifact Version identifier")
        if self.created_by.kind is not ResourceKind.PRINCIPAL:
            raise ValueError("invalid alias creator")
        if self.updated_by.kind is not ResourceKind.PRINCIPAL:
            raise ValueError("invalid alias updater")
        if normalize_artifact_alias(self.alias) != self.alias:
            raise ValueError("Artifact alias must be normalized")


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
    sequence: int = 1
    mlflow_model_id: str = ""
    producing_run_id: PublicId | None = None
    model_signature: dict[str, object] | None = None
    model_signature_sha256: str | None = None

    def __post_init__(self) -> None:
        if self.id.kind is not ResourceKind.ARTIFACT_VERSION:
            raise ValueError("invalid Artifact Version identifier")
        if self.artifact_id.kind is not ResourceKind.ARTIFACT:
            raise ValueError("invalid Artifact identifier")
        if self.owning_project_id.kind is not ResourceKind.PROJECT:
            raise ValueError("invalid owning project identifier")
        if self.sequence < 1:
            raise ValueError("Artifact Version sequence must be positive")
        if self.mlflow_model_id and not self.mlflow_model_id.startswith("m-"):
            raise ValueError("invalid MLflow model identifier")
        if self.producing_run_id is not None and self.producing_run_id.kind is not ResourceKind.RUN:
            raise ValueError("invalid producing Run identifier")
        if (self.model_signature is None) != (self.model_signature_sha256 is None):
            raise ValueError("model signature and digest must be stored together")
        if self.model_signature_sha256 is not None and (
            len(self.model_signature_sha256) != 64
            or any(character not in "0123456789abcdef" for character in self.model_signature_sha256)
        ):
            raise ValueError("invalid model signature digest")
