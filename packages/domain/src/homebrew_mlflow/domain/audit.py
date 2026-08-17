from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .identifiers import PublicId
from .identity import utc_now


@dataclass(frozen=True, slots=True)
class AuditEvent:
    actor_principal_id: PublicId
    action: str
    resource_type: str
    resource_id: PublicId | None
    outcome: str
    request_id: PublicId
    project_id: PublicId | None = None
    safe_metadata: dict[str, Any] = field(default_factory=dict)
    occurred_at: datetime = field(default_factory=utc_now)
