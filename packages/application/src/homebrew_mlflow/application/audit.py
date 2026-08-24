from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from homebrew_mlflow.domain import ProjectRole, PublicId

from .projects import AuthorizationDenied


@dataclass(frozen=True, slots=True)
class AuditEventView:
    sequence: int
    occurred_at: datetime
    actor_principal_id: PublicId | None
    project_id: PublicId
    action: str
    resource_type: str
    resource_id: str | None
    outcome: str
    request_id: PublicId
    safe_metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class AuditEventPage:
    items: tuple[AuditEventView, ...]
    total_count: int
    next_before_sequence: int | None


class AuditUnitOfWork(Protocol):
    def project_role(self, project_id: PublicId, principal_id: PublicId) -> ProjectRole | None: ...

    def events(
        self, project_id: PublicId, *, after_sequence: int, limit: int
    ) -> tuple[AuditEventView, ...]: ...

    def recent_events(
        self, project_id: PublicId, *, before_sequence: int | None, limit: int
    ) -> tuple[AuditEventView, ...]: ...

    def event_count(self, project_id: PublicId) -> int: ...


class AuditService:
    def __init__(self, unit_of_work: AuditUnitOfWork) -> None:
        self._uow = unit_of_work

    def list(
        self,
        actor_id: PublicId,
        project_id: PublicId,
        *,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> tuple[AuditEventView, ...]:
        if self._uow.project_role(project_id, actor_id) is None:
            raise AuthorizationDenied("project membership is required")
        return self._uow.events(
            project_id,
            after_sequence=max(0, after_sequence),
            limit=max(1, min(limit, 200)),
        )

    def recent_page(
        self,
        actor_id: PublicId,
        project_id: PublicId,
        *,
        before_sequence: int | None = None,
        limit: int = 100,
    ) -> AuditEventPage:
        if self._uow.project_role(project_id, actor_id) is None:
            raise AuthorizationDenied("project membership is required")
        bounded_limit = max(1, min(limit, 200))
        values = self._uow.recent_events(
            project_id,
            before_sequence=(
                max(1, before_sequence) if before_sequence is not None else None
            ),
            limit=bounded_limit + 1,
        )
        has_more = len(values) > bounded_limit
        items = values[:bounded_limit]
        return AuditEventPage(
            items=items,
            total_count=self._uow.event_count(project_id),
            next_before_sequence=(items[-1].sequence if has_more and items else None),
        )
