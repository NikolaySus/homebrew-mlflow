from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from homebrew_mlflow.domain import (
    ArtifactVersion,
    AvailabilityState,
    DvcOutputIdentity,
    IntegrityState,
    PublicationOperation,
    PublicationState,
    PublicId,
    ResourceKind,
    normalize_file_index,
)

logger = logging.getLogger(__name__)


class PublicationValidationError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class ValidatedFile:
    path: str
    size: int
    digest: str | None = None


@dataclass(frozen=True, slots=True)
class ValidatedPublication:
    artifact_id: PublicId
    identity: DvcOutputIdentity
    files: tuple[ValidatedFile, ...]
    bucket: str
    object_key: str
    producing_run_id: PublicId | None = None
    model_signature: dict[str, Any] | None = None
    model_signature_sha256: str | None = None

    def __post_init__(self) -> None:
        normalize_file_index([item.path for item in self.files])
        if sum(item.size for item in self.files) != self.identity.size:
            raise ValueError("validated file sizes do not equal the DVC output size")
        if len(self.files) != self.identity.file_count:
            raise ValueError("validated file count does not equal the DVC output count")
        if (self.model_signature is None) != (self.model_signature_sha256 is None):
            raise ValueError("model signature and digest must be provided together")


class PublicationValidator(Protocol):
    def validate(self, operation: PublicationOperation) -> ValidatedPublication: ...


class PublicationWorkStore(Protocol):
    def claim_next(self, worker_id: str, now: datetime) -> PublicationOperation | None: ...

    def advance(
        self,
        operation: PublicationOperation,
        target: PublicationState,
        event_name: str,
        now: datetime,
        payload: dict[str, Any] | None = None,
    ) -> PublicationOperation: ...

    def publish(
        self, operation: PublicationOperation, validated: ValidatedPublication, now: datetime
    ) -> ArtifactVersion: ...

    def fail(
        self, operation: PublicationOperation, failure_code: str, now: datetime
    ) -> PublicationOperation: ...


class PublicationCoordinator:
    def __init__(self, store: PublicationWorkStore, validator: PublicationValidator) -> None:
        self._store = store
        self._validator = validator

    def run_once(self, worker_id: str, now: datetime) -> bool:
        operation = self._store.claim_next(worker_id, now)
        if operation is None:
            return False
        try:
            operation = self._store.advance(
                operation, PublicationState.RESOLVING, "operation.resolving", now
            )
            validated = self._validator.validate(operation)
            operation = self._store.advance(
                operation,
                PublicationState.VERIFYING,
                "validation.progress",
                now,
                {
                    "verified_bytes": validated.identity.size,
                    "total_bytes": validated.identity.size,
                    "verified_objects": validated.identity.file_count,
                    "total_objects": validated.identity.file_count,
                },
            )
            operation = self._store.advance(
                operation, PublicationState.COMMITTING, "operation.committing", now
            )
            self._store.publish(operation, validated, now)
        except PublicationValidationError as error:
            self._store.fail(operation, error.code, now)
        except Exception:
            logger.exception(
                "unexpected publication worker failure for operation %s",
                operation.id,
            )
            self._store.fail(operation, "worker_failed", now)
        return True


def artifact_version_from_validation(
    operation: PublicationOperation, validated: ValidatedPublication, now: datetime
) -> ArtifactVersion:
    return ArtifactVersion(
        PublicId.generate(ResourceKind.ARTIFACT_VERSION),
        validated.artifact_id,
        operation.project_id,
        validated.identity,
        IntegrityState.VERIFIED,
        AvailabilityState.AVAILABLE,
        now,
        model_signature=validated.model_signature,
        model_signature_sha256=validated.model_signature_sha256,
    )
