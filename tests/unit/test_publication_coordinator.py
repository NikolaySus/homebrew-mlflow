from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from homebrew_mlflow.application import (
    PublicationCoordinator,
    PublicationValidationError,
    ValidatedFile,
    ValidatedPublication,
    artifact_version_from_validation,
)
from homebrew_mlflow.domain import (
    DvcOutputIdentity,
    OutputKind,
    PublicationOperation,
    PublicationState,
    PublicId,
    ResourceKind,
)

NOW = datetime(2026, 8, 17, 12, tzinfo=UTC)


@dataclass
class Store:
    operation: PublicationOperation | None
    events: list[str] = field(default_factory=list)
    failure_code: str | None = None

    def claim_next(self, _worker_id: str, _now: datetime) -> PublicationOperation | None:
        value, self.operation = self.operation, None
        return value

    def advance(
        self,
        operation: PublicationOperation,
        target: PublicationState,
        event_name: str,
        _now: datetime,
        _payload: dict[str, Any] | None = None,
    ) -> PublicationOperation:
        updated = operation.transition(target)
        self.events.append(event_name)
        return updated

    def publish(self, operation, validated, now):  # type: ignore[no-untyped-def]
        published = operation.transition(PublicationState.PUBLISHED)
        self.events.append("operation.published")
        self.operation = published
        return artifact_version_from_validation(operation, validated, now)

    def fail(
        self, operation: PublicationOperation, failure_code: str, _now: datetime
    ) -> PublicationOperation:
        failed = operation.transition(PublicationState.FAILED)
        self.events.append("operation.failed")
        self.failure_code = failure_code
        self.operation = failed
        return failed


@dataclass
class Validator:
    result: ValidatedPublication | None = None
    failure_code: str | None = None
    unexpected: bool = False

    def validate(self, _operation: PublicationOperation) -> ValidatedPublication:
        if self.unexpected:
            raise RuntimeError("sensitive internal detail")
        if self.failure_code is not None:
            raise PublicationValidationError(self.failure_code)
        assert self.result is not None
        return self.result


def queued() -> PublicationOperation:
    return PublicationOperation.queued(
        PublicId.generate(ResourceKind.PROJECT), "key", "a" * 64, {"selector": {}}
    )


def test_coordinator_commits_one_immutable_artifact_version() -> None:
    operation = queued()
    validated = ValidatedPublication(
        PublicId.generate(ResourceKind.ARTIFACT),
        DvcOutputIdentity("md5", "b" * 32, OutputKind.FILE, 4, 1),
        (ValidatedFile("model.bin", 4, "b" * 32),),
        "research",
        "dvc/project/files/md5/bb/rest",
    )
    store = Store(operation)

    assert PublicationCoordinator(store, Validator(validated)).run_once("worker", NOW)
    assert store.operation is not None
    assert store.operation.state is PublicationState.PUBLISHED
    assert store.events == [
        "operation.resolving",
        "validation.progress",
        "operation.committing",
        "operation.published",
    ]


def test_coordinator_persists_stable_validation_failure() -> None:
    store = Store(queued())

    PublicationCoordinator(store, Validator(failure_code="object_missing")).run_once("worker", NOW)

    assert store.operation is not None and store.operation.state is PublicationState.FAILED
    assert store.failure_code == "object_missing"


def test_coordinator_maps_unexpected_failure_to_safe_code() -> None:
    store = Store(queued())

    PublicationCoordinator(store, Validator(unexpected=True)).run_once("worker", NOW)

    assert store.operation is not None and store.operation.state is PublicationState.FAILED
    assert store.failure_code == "worker_failed"
