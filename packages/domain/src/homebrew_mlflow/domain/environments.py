from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from .identifiers import PublicId, ResourceKind


class EnvironmentKind(StrEnum):
    PIP = "pip"
    CONDA = "conda"
    CONTAINER = "container"
    SYSTEM = "system"


@dataclass(frozen=True, slots=True)
class EnvironmentSpecification:
    id: PublicId
    project_id: PublicId
    name: str
    kind: EnvironmentKind
    canonical_document: str
    sha256: str
    created_at: datetime
    archived_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.id.kind is not ResourceKind.ENVIRONMENT_SPECIFICATION:
            raise ValueError("invalid Environment Specification identifier")
        if self.project_id.kind is not ResourceKind.PROJECT:
            raise ValueError("Environment Specification must belong to a Research Project")
        if not self.name.strip() or len(self.name) > 200:
            raise ValueError("Environment Specification name must contain 1 to 200 characters")
        if not self.canonical_document:
            raise ValueError("Environment Specification document is required")
        if len(self.sha256) != 64 or any(c not in "0123456789abcdef" for c in self.sha256):
            raise ValueError("Environment Specification SHA-256 is invalid")
