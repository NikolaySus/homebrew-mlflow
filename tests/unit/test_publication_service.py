from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest
from homebrew_mlflow.application import EventHistoryExpired, PublicationService, ResourceConflict
from homebrew_mlflow.domain import (
    AuditEvent,
    ProjectRole,
    PublicationEvent,
    PublicationOperation,
    PublicId,
    ResourceKind,
)


@dataclass
class MemoryPublicationUnitOfWork:
    operations: list[PublicationOperation] = field(default_factory=list)
    events: list[PublicationEvent] = field(default_factory=list)
    audits: list[AuditEvent] = field(default_factory=list)
    commits: int = 0
    expired_through: int = 0
    actor_id: PublicId = field(default_factory=lambda: PublicId.generate(ResourceKind.PRINCIPAL))

    def project_role(self, _project_id: PublicId, principal_id: PublicId) -> ProjectRole | None:
        return ProjectRole.CONTRIBUTOR if principal_id == self.actor_id else None

    def find_by_idempotency_key(
        self, project_id: PublicId, key: str
    ) -> PublicationOperation | None:
        return next(
            (
                operation
                for operation in self.operations
                if operation.project_id == project_id and operation.idempotency_key == key
            ),
            None,
        )

    def add_operation(self, operation: PublicationOperation) -> None:
        self.operations.append(operation)

    def add_event(self, event: PublicationEvent) -> None:
        self.events.append(event)

    def operation(self, operation_id: PublicId) -> PublicationOperation | None:
        return next((item for item in self.operations if item.id == operation_id), None)

    def events_after(self, operation_id: PublicId, sequence: int) -> tuple[PublicationEvent, ...]:
        return tuple(
            item
            for item in self.events
            if item.operation_id == operation_id and item.sequence > sequence
        )

    def event_history_expired_through(self, _operation_id: PublicId) -> int:
        return self.expired_through

    def append_audit(self, event: AuditEvent) -> None:
        self.audits.append(event)

    def commit(self) -> None:
        self.commits += 1


def test_same_idempotency_key_and_payload_replays_without_new_validation() -> None:
    uow = MemoryPublicationUnitOfWork()
    service = PublicationService(uow)
    project_id = PublicId.generate(ResourceKind.PROJECT)
    now = datetime(2026, 8, 13, tzinfo=UTC)
    payload = {"artifact_id": "ar_1", "selector": {"kind": "pipeline-output"}}

    first = service.create(uow.actor_id, project_id, "key", payload, now)
    replay = service.create(
        uow.actor_id,
        project_id,
        "key",
        {"selector": {"kind": "pipeline-output"}, "artifact_id": "ar_1"},
        now,
    )
    assert not first.replayed
    assert replay.replayed
    assert replay.operation.id == first.operation.id
    assert len(uow.events) == 1
    assert uow.commits == 1


def test_reusing_idempotency_key_with_different_payload_conflicts() -> None:
    uow = MemoryPublicationUnitOfWork()
    service = PublicationService(uow)
    project_id = PublicId.generate(ResourceKind.PROJECT)
    now = datetime(2026, 8, 13, tzinfo=UTC)
    service.create(uow.actor_id, project_id, "key", {"artifact_id": "ar_1"}, now)
    with pytest.raises(ResourceConflict, match="different request"):
        service.create(uow.actor_id, project_id, "key", {"artifact_id": "ar_2"}, now)


def test_event_replay_before_retained_boundary_expires() -> None:
    uow = MemoryPublicationUnitOfWork(expired_through=3)
    service = PublicationService(uow)
    project_id = PublicId.generate(ResourceKind.PROJECT)
    operation = service.create(
        uow.actor_id, project_id, "key", {"artifact_id": "ar_1"}, datetime.now(UTC)
    ).operation

    with pytest.raises(EventHistoryExpired):
        service.events(uow.actor_id, operation.id, 2)
    assert service.events(uow.actor_id, operation.id, 3) == ()
