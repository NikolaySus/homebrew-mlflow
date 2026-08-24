from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from homebrew_mlflow.application import AuditEventView, AuditService, AuthorizationDenied
from homebrew_mlflow.domain import ProjectRole, PublicId, ResourceKind


@dataclass
class AuditStore:
    actor_id: PublicId
    project_id: PublicId
    values: tuple[AuditEventView, ...]
    role: ProjectRole | None = ProjectRole.VIEWER

    def project_role(
        self, project_id: PublicId, principal_id: PublicId
    ) -> ProjectRole | None:
        if project_id == self.project_id and principal_id == self.actor_id:
            return self.role
        return None

    def events(
        self, project_id: PublicId, *, after_sequence: int, limit: int
    ) -> tuple[AuditEventView, ...]:
        return tuple(
            event
            for event in sorted(self.values, key=lambda value: value.sequence)
            if event.project_id == project_id and event.sequence > after_sequence
        )[:limit]

    def recent_events(
        self, project_id: PublicId, *, before_sequence: int | None, limit: int
    ) -> tuple[AuditEventView, ...]:
        return tuple(
            event
            for event in sorted(
                self.values, key=lambda value: value.sequence, reverse=True
            )
            if event.project_id == project_id
            and (before_sequence is None or event.sequence < before_sequence)
        )[:limit]

    def event_count(self, project_id: PublicId) -> int:
        return sum(event.project_id == project_id for event in self.values)


def _events(project_id: PublicId) -> tuple[AuditEventView, ...]:
    request_id = PublicId.generate(ResourceKind.REQUEST)
    return tuple(
        AuditEventView(
            sequence=sequence,
            occurred_at=datetime(2026, 8, sequence, tzinfo=UTC),
            actor_principal_id=None,
            project_id=project_id,
            action=f"event.{sequence}",
            resource_type="test",
            resource_id=str(sequence),
            outcome="success",
            request_id=request_id,
            safe_metadata={},
        )
        for sequence in range(1, 6)
    )


def test_recent_audit_pages_are_newest_first_with_exclusive_cursors() -> None:
    actor_id = PublicId.generate(ResourceKind.PRINCIPAL)
    project_id = PublicId.generate(ResourceKind.PROJECT)
    service = AuditService(AuditStore(actor_id, project_id, _events(project_id)))

    first = service.recent_page(actor_id, project_id, limit=2)
    assert [event.sequence for event in first.items] == [5, 4]
    assert first.total_count == 5
    assert first.next_before_sequence == 4

    second = service.recent_page(
        actor_id, project_id, before_sequence=first.next_before_sequence, limit=2
    )
    assert [event.sequence for event in second.items] == [3, 2]
    assert second.next_before_sequence == 2

    last = service.recent_page(
        actor_id, project_id, before_sequence=second.next_before_sequence, limit=2
    )
    assert [event.sequence for event in last.items] == [1]
    assert last.next_before_sequence is None


def test_recent_audit_page_requires_current_project_membership() -> None:
    actor_id = PublicId.generate(ResourceKind.PRINCIPAL)
    project_id = PublicId.generate(ResourceKind.PROJECT)
    service = AuditService(
        AuditStore(actor_id, project_id, _events(project_id), role=None)
    )

    with pytest.raises(AuthorizationDenied):
        service.recent_page(actor_id, project_id)
