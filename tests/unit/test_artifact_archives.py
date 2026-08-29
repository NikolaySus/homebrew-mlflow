from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from homebrew_mlflow.application import (
    ArtifactArchiveCapacityUnavailable,
    ArtifactArchiveFile,
    ArtifactArchiveJob,
    ArtifactArchiveService,
    ArtifactArchiveSource,
    ArtifactArchiveState,
    ArtifactArchiveTooLarge,
)
from homebrew_mlflow.domain import (
    ArtifactVersion,
    AvailabilityState,
    DvcOutputIdentity,
    IntegrityState,
    OutputKind,
    PublicId,
    ResourceKind,
)

NOW = datetime(2026, 8, 29, 12, tzinfo=UTC)


def source(size: int = 12, count: int = 1) -> ArtifactArchiveSource:
    version = ArtifactVersion(
        PublicId.generate(ResourceKind.ARTIFACT_VERSION),
        PublicId.generate(ResourceKind.ARTIFACT),
        PublicId.generate(ResourceKind.PROJECT),
        DvcOutputIdentity("md5", "a" * 32, OutputKind.FILE, size, count),
        IntegrityState.VERIFIED,
        AvailabilityState.AVAILABLE,
        NOW,
    )
    return ArtifactArchiveSource(
        version, "predictions", "research",
        (ArtifactArchiveFile("predictions.csv", size, "a" * 32, "dvc/key"),),
    )


@dataclass
class Store:
    value: ArtifactArchiveSource
    reserved: int = 0
    job: ArtifactArchiveJob | None = None
    committed: bool = False

    def version(self, version_id: PublicId) -> ArtifactVersion | None:
        return self.value.version if version_id == self.value.version.id else None

    def version_accessible(self, version_id: PublicId, _principal_id: PublicId) -> bool:
        return version_id == self.value.version.id

    def archive_source(self, version_id: PublicId) -> ArtifactArchiveSource | None:
        return self.value if version_id == self.value.version.id else None

    def archive_job(self, _version_id: PublicId) -> ArtifactArchiveJob | None:
        return self.job

    def reserved_archive_bytes(self, _now: datetime, excluding=None):  # type: ignore[no-untyped-def]
        return self.reserved

    def queue_archive(
        self,
        version_id: PublicId,
        total_bytes: int,
        _reserved_bytes: int,
        _cache_bytes: int,
        now: datetime,
    ) -> None:
        self.job = ArtifactArchiveJob(
            version_id, ArtifactArchiveState.PENDING, 0, total_bytes, None, None, now, now
        )

    def commit(self) -> None:
        self.committed = True


def actor() -> PublicId:
    return PublicId.generate(ResourceKind.PRINCIPAL)


def test_request_queues_one_authorized_archive_and_is_idempotent() -> None:
    store = Store(source())
    service = ArtifactArchiveService(store, max_bytes=100, max_files=10, cache_bytes=2_000_000)

    first = service.request(actor(), store.value.version.id, NOW)
    second = service.request(actor(), store.value.version.id, NOW)

    assert first.state is ArtifactArchiveState.PENDING
    assert second == first
    assert store.committed


def test_request_enforces_version_and_shared_cache_limits() -> None:
    oversized = Store(source(size=101))
    with pytest.raises(ArtifactArchiveTooLarge):
        ArtifactArchiveService(
            oversized, max_bytes=100, max_files=10, cache_bytes=2_000_000
        ).request(actor(), oversized.value.version.id, NOW)

    full = Store(source(), reserved=2_000_000)
    with pytest.raises(ArtifactArchiveCapacityUnavailable):
        ArtifactArchiveService(
            full, max_bytes=100, max_files=10, cache_bytes=2_000_000
        ).request(actor(), full.value.version.id, NOW)
