from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .identifiers import PublicId, ResourceKind
from .paths import normalize_artifact_path


@dataclass(frozen=True, slots=True)
class SecretContext:
    project_id: PublicId
    infisical_project_id: str
    environment_slug: str
    secret_path: str
    updated_at: datetime
    reconciliation_state: str = "queued"
    last_error_code: str | None = None

    def __post_init__(self) -> None:
        if self.project_id.kind is not ResourceKind.PROJECT:
            raise ValueError("Secret Context must belong to a Research Project")
        if not self.infisical_project_id.strip() or len(self.infisical_project_id) > 200:
            raise ValueError("Infisical project ID is required")
        if not self.environment_slug.strip() or len(self.environment_slug) > 100:
            raise ValueError("Infisical environment slug is required")
        normalized = self.secret_path.strip().strip("/")
        if normalized:
            normalize_artifact_path(normalized)
        if self.reconciliation_state not in {"queued", "in_sync", "drift", "failed"}:
            raise ValueError("invalid Secret Context reconciliation state")
