from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from typing import Any

from .identifiers import PublicId, ResourceKind
from .identity import utc_now


class PublicationState(StrEnum):
    QUEUED = "queued"
    RESOLVING = "resolving"
    VERIFYING = "verifying"
    COMMITTING = "committing"
    PUBLISHED = "published"
    FAILED = "failed"


_TRANSITIONS = {
    PublicationState.QUEUED: frozenset({PublicationState.RESOLVING, PublicationState.FAILED}),
    PublicationState.RESOLVING: frozenset({PublicationState.VERIFYING, PublicationState.FAILED}),
    PublicationState.VERIFYING: frozenset({PublicationState.COMMITTING, PublicationState.FAILED}),
    PublicationState.COMMITTING: frozenset({PublicationState.PUBLISHED, PublicationState.FAILED}),
    PublicationState.PUBLISHED: frozenset(),
    PublicationState.FAILED: frozenset(),
}


class InvalidPublicationTransition(ValueError):
    pass


def transition_publication(current: PublicationState, target: PublicationState) -> PublicationState:
    if target not in _TRANSITIONS[current]:
        raise InvalidPublicationTransition(
            f"cannot transition Publication from {current} to {target}"
        )
    return target


@dataclass(frozen=True, slots=True)
class PublicationOperation:
    id: PublicId
    project_id: PublicId
    idempotency_key: str
    request_digest: str
    request_payload: dict[str, Any]
    state: PublicationState
    created_at: datetime
    created_by: PublicId | None = None

    def transition(self, target: PublicationState) -> PublicationOperation:
        return replace(self, state=transition_publication(self.state, target))

    @classmethod
    def queued(
        cls,
        project_id: PublicId,
        idempotency_key: str,
        request_digest: str,
        request_payload: dict[str, Any],
        created_by: PublicId | None = None,
    ) -> PublicationOperation:
        if project_id.kind is not ResourceKind.PROJECT:
            raise ValueError("publication must belong to a Research Project")
        if not idempotency_key or len(idempotency_key) > 200:
            raise ValueError("idempotency key must contain 1 to 200 characters")
        if len(request_digest) != 64:
            raise ValueError("publication request digest must be SHA-256")
        return cls(
            PublicId.generate(ResourceKind.PUBLICATION),
            project_id,
            idempotency_key,
            request_digest,
            request_payload,
            PublicationState.QUEUED,
            utc_now(),
            created_by,
        )


@dataclass(frozen=True, slots=True)
class PublicationEvent:
    operation_id: PublicId
    sequence: int
    name: str
    occurred_at: datetime
    payload: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.operation_id.kind is not ResourceKind.PUBLICATION or self.sequence < 1:
            raise ValueError("invalid publication event identity")
