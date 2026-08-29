from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol

from homebrew_mlflow.domain import ArtifactVersion, PublicId


class ArtifactArchiveState(StrEnum):
    PENDING = "pending"
    BUILDING = "building"
    READY = "ready"
    FAILED = "failed"
    EXPIRED = "expired"


class ArtifactArchiveTooLarge(ValueError):
    pass


class ArtifactArchiveCapacityUnavailable(RuntimeError):
    pass


class ArtifactArchiveNotReady(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ArtifactArchiveFile:
    path: str
    size: int
    digest: str
    object_key: str


@dataclass(frozen=True, slots=True)
class ArtifactArchiveSource:
    version: ArtifactVersion
    artifact_name: str
    bucket: str
    files: tuple[ArtifactArchiveFile, ...]


@dataclass(frozen=True, slots=True)
class ArtifactArchiveJob:
    version_id: PublicId
    state: ArtifactArchiveState
    processed_bytes: int
    total_bytes: int
    archive_size: int | None
    object_key: str | None
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None = None
    failure_code: str | None = None


@dataclass(frozen=True, slots=True)
class BuiltArtifactArchive:
    object_key: str
    size: int


class ArtifactArchiveUnitOfWork(Protocol):
    def version(self, version_id: PublicId) -> ArtifactVersion | None: ...

    def version_accessible(self, version_id: PublicId, principal_id: PublicId) -> bool: ...

    def archive_source(self, version_id: PublicId) -> ArtifactArchiveSource | None: ...

    def archive_job(self, version_id: PublicId) -> ArtifactArchiveJob | None: ...

    def reserved_archive_bytes(self, now: datetime, excluding: PublicId | None = None) -> int: ...

    def queue_archive(
        self,
        version_id: PublicId,
        total_bytes: int,
        reserved_bytes: int,
        cache_bytes: int,
        now: datetime,
    ) -> None: ...

    def commit(self) -> None: ...


class ArtifactArchiveWorkStore(Protocol):
    def expired_ready(self, now: datetime) -> tuple[tuple[PublicId, str], ...]: ...

    def mark_expired(self, version_id: PublicId, now: datetime) -> None: ...

    def recover_stale(self, before: datetime, now: datetime) -> int: ...

    def claim_next(self, worker_id: str, now: datetime) -> ArtifactArchiveSource | None: ...

    def update_progress(
        self, version_id: PublicId, processed_bytes: int, now: datetime
    ) -> None: ...

    def complete(
        self,
        version_id: PublicId,
        built: BuiltArtifactArchive,
        expires_at: datetime,
        now: datetime,
    ) -> None: ...

    def fail(self, version_id: PublicId, failure_code: str, now: datetime) -> None: ...


class ArtifactArchiveBuilder(Protocol):
    def build(
        self,
        source: ArtifactArchiveSource,
        progress: Callable[[int], None],
    ) -> BuiltArtifactArchive: ...

    def delete(self, object_key: str) -> None: ...


class ArtifactArchiveService:
    def __init__(
        self,
        unit_of_work: ArtifactArchiveUnitOfWork,
        *,
        max_bytes: int,
        max_files: int,
        cache_bytes: int,
    ) -> None:
        self._uow = unit_of_work
        self._max_bytes = max_bytes
        self._max_files = max_files
        self._cache_bytes = cache_bytes

    def request(
        self, actor_id: PublicId, version_id: PublicId, now: datetime
    ) -> ArtifactArchiveJob:
        source = self._authorized_source(actor_id, version_id)
        if source.version.identity.size > self._max_bytes:
            raise ArtifactArchiveTooLarge("artifact version exceeds the ZIP size limit")
        if source.version.identity.file_count > self._max_files:
            raise ArtifactArchiveTooLarge("artifact version exceeds the ZIP file-count limit")
        current = self._uow.archive_job(version_id)
        if current is not None:
            if current.state in {ArtifactArchiveState.PENDING, ArtifactArchiveState.BUILDING}:
                return current
            if (
                current.state is ArtifactArchiveState.READY
                and current.expires_at is not None
                and current.expires_at > now
            ):
                return current
        reservation = self._estimate_size(source)
        if (
            self._uow.reserved_archive_bytes(now, excluding=version_id) + reservation
            > self._cache_bytes
        ):
            raise ArtifactArchiveCapacityUnavailable("artifact ZIP cache capacity is unavailable")
        self._uow.queue_archive(
            version_id, source.version.identity.size, reservation, self._cache_bytes, now
        )
        self._uow.commit()
        queued = self._uow.archive_job(version_id)
        if queued is None:
            raise RuntimeError("archive job was not persisted")
        return queued

    def status(self, actor_id: PublicId, version_id: PublicId, now: datetime) -> ArtifactArchiveJob:
        self._authorized_source(actor_id, version_id)
        job = self._uow.archive_job(version_id)
        if job is None:
            raise ValueError("artifact archive has not been requested")
        if (
            job.state is ArtifactArchiveState.READY
            and job.expires_at is not None
            and job.expires_at <= now
        ):
            return ArtifactArchiveJob(
                job.version_id,
                ArtifactArchiveState.EXPIRED,
                job.processed_bytes,
                job.total_bytes,
                job.archive_size,
                job.object_key,
                job.created_at,
                job.updated_at,
                job.expires_at,
                job.failure_code,
            )
        return job

    def ready(
        self, actor_id: PublicId, version_id: PublicId, now: datetime
    ) -> tuple[ArtifactArchiveJob, ArtifactArchiveSource]:
        source = self._authorized_source(actor_id, version_id)
        job = self.status(actor_id, version_id, now)
        if (
            job.state is not ArtifactArchiveState.READY
            or job.object_key is None
            or job.expires_at is None
            or job.expires_at <= now
        ):
            raise ArtifactArchiveNotReady("artifact ZIP is not ready")
        return job, source

    def _authorized_source(self, actor_id: PublicId, version_id: PublicId) -> ArtifactArchiveSource:
        version = self._uow.version(version_id)
        if version is None or not self._uow.version_accessible(version_id, actor_id):
            raise ValueError("Artifact Version bytes are not accessible")
        source = self._uow.archive_source(version_id)
        if source is None:
            raise ValueError("Artifact Version bytes are not accessible")
        return source

    @staticmethod
    def _estimate_size(source: ArtifactArchiveSource) -> int:
        return source.version.identity.size + len(source.files) * 256 + 1024**2


class ArtifactArchiveCoordinator:
    def __init__(
        self,
        store: ArtifactArchiveWorkStore,
        builder: ArtifactArchiveBuilder,
        *,
        retention: timedelta = timedelta(hours=24),
        stale_after: timedelta = timedelta(minutes=15),
    ) -> None:
        self._store = store
        self._builder = builder
        self._retention = retention
        self._stale_after = stale_after

    def run_once(self, worker_id: str, now: datetime) -> bool:
        for version_id, object_key in self._store.expired_ready(now):
            try:
                self._builder.delete(object_key)
            except Exception:
                continue
            self._store.mark_expired(version_id, now)
        self._store.recover_stale(now - self._stale_after, now)
        source = self._store.claim_next(worker_id, now)
        if source is None:
            return False
        last_progress = 0

        def progress(processed: int) -> None:
            nonlocal last_progress
            if (
                processed == source.version.identity.size
                or processed - last_progress >= 8 * 1024**2
            ):
                self._store.update_progress(source.version.id, processed, datetime.now(now.tzinfo))
                last_progress = processed

        try:
            built = self._builder.build(source, progress)
            completed = datetime.now(now.tzinfo)
            self._store.complete(
                source.version.id,
                built,
                completed + self._retention,
                completed,
            )
        except Exception as error:
            code = getattr(error, "code", "worker_failed")
            self._store.fail(source.version.id, str(code), datetime.now(now.tzinfo))
        return True
