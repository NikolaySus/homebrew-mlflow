from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .identifiers import PublicId, ResourceKind
from .paths import normalize_artifact_path


@dataclass(frozen=True, slots=True)
class RunAttachment:
    run_id: PublicId
    path: str
    size: int
    media_type: str
    sha256: str
    object_key: str
    created_at: datetime
    purged_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.run_id.kind is not ResourceKind.RUN:
            raise ValueError("attachment must belong to a Run")
        object.__setattr__(self, "path", normalize_artifact_path(self.path))
        if self.size < 0:
            raise ValueError("attachment size must be non-negative")
        if len(self.sha256) != 64 or any(c not in "0123456789abcdef" for c in self.sha256):
            raise ValueError("attachment SHA-256 is invalid")
        if not self.media_type or len(self.media_type) > 200:
            raise ValueError("attachment media type is invalid")
        if not self.object_key:
            raise ValueError("attachment object key is required")
