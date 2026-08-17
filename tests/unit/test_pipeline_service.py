import hashlib
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

import pytest
from homebrew_mlflow.application import AuthorizationDenied, PipelineService, ResourceConflict
from homebrew_mlflow.domain import (
    AuditEvent,
    PipelineDefinition,
    PipelineVersion,
    ProjectRole,
    PublicId,
    ResourceKind,
)

NOW = datetime(2026, 8, 17, 12, tzinfo=UTC)


@dataclass
class PipelineStore:
    actor: PublicId
    project: PublicId
    repository: PublicId
    role: ProjectRole | None
    definitions_by_id: dict[PublicId, PipelineDefinition] = field(default_factory=dict)
    version_values: list[PipelineVersion] = field(default_factory=list)
    audits: list[AuditEvent] = field(default_factory=list)

    def project_role(self, project_id: PublicId, principal_id: PublicId) -> ProjectRole | None:
        return self.role if (project_id, principal_id) == (self.project, self.actor) else None

    def repository_project(self, repository_id: PublicId) -> PublicId | None:
        return self.project if repository_id == self.repository else None

    def definition(self, definition_id: PublicId) -> PipelineDefinition | None:
        return self.definitions_by_id.get(definition_id)

    def definition_by_name(
        self, _project_id: PublicId, name: str
    ) -> PipelineDefinition | None:
        return next(
            (
                value
                for value in self.definitions_by_id.values()
                if value.name.lower() == name.lower()
            ),
            None,
        )

    def definition_name_exists(self, _project_id: PublicId, name: str) -> bool:
        return any(value.name.lower() == name.lower() for value in self.definitions_by_id.values())

    def version_exists(
        self, definition_id: PublicId, repository_id: PublicId, commit: str, path: str
    ) -> bool:
        return any(
            (value.definition_id, value.repository_id, value.git_commit_sha, value.pipeline_path)
            == (definition_id, repository_id, commit, path)
            for value in self.version_values
        )

    def version_by_source(
        self, definition_id: PublicId, repository_id: PublicId, commit: str, path: str
    ) -> PipelineVersion | None:
        return next(
            (
                value
                for value in self.version_values
                if (
                    value.definition_id,
                    value.repository_id,
                    value.git_commit_sha,
                    value.pipeline_path,
                )
                == (definition_id, repository_id, commit, path)
            ),
            None,
        )

    def definitions(
        self, _project_id: PublicId, *, include_archived: bool
    ) -> tuple[PipelineDefinition, ...]:
        return tuple(
            value
            for value in self.definitions_by_id.values()
            if include_archived or value.archived_at is None
        )

    def versions(
        self, definition_id: PublicId, *, include_archived: bool
    ) -> tuple[PipelineVersion, ...]:
        return tuple(
            value
            for value in self.version_values
            if value.definition_id == definition_id
            and (include_archived or value.archived_at is None)
        )

    def add_definition(self, definition: PipelineDefinition) -> None:
        self.definitions_by_id[definition.id] = definition

    def add_version(self, version: PipelineVersion) -> None:
        self.version_values.append(version)

    def archive_definition(self, definition_id: PublicId, at: datetime) -> None:
        self.definitions_by_id[definition_id] = replace(
            self.definitions_by_id[definition_id], archived_at=at
        )

    def archive_version(self, version_id: PublicId, at: datetime) -> None:
        self.version_values = [
            replace(value, archived_at=at) if value.id == version_id else value
            for value in self.version_values
        ]

    def append_audit(self, event: AuditEvent) -> None:
        self.audits.append(event)

    def commit(self) -> None:
        return None


@dataclass
class SourceReader:
    content: bytes
    reads: list[tuple[PublicId, str, str]] = field(default_factory=list)

    def read(self, repository_id: PublicId, commit: str, path: str) -> bytes:
        self.reads.append((repository_id, commit, path))
        return self.content


def _store(role: ProjectRole = ProjectRole.CONTRIBUTOR) -> PipelineStore:
    return PipelineStore(
        PublicId.generate(ResourceKind.PRINCIPAL),
        PublicId.generate(ResourceKind.PROJECT),
        PublicId.generate(ResourceKind.REPOSITORY),
        role,
    )


def test_pipeline_version_hash_is_derived_from_exact_committed_source() -> None:
    store = _store()
    source = SourceReader(b"stages:\n  train:\n    cmd: python train.py\n")
    request = PublicId.generate(ResourceKind.REQUEST)
    definition = PipelineService(store).create_definition(
        store.actor, store.project, "training", request, NOW
    )

    version = PipelineService(store, source).register_version(
        store.actor,
        definition.id,
        store.repository,
        "a" * 40,
        "dvc.yaml",
        request,
        NOW,
    )

    assert version.content_sha256 == hashlib.sha256(source.content).hexdigest()
    assert source.reads == [(store.repository, "a" * 40, "dvc.yaml")]
    assert [event.action for event in store.audits] == ["pipeline.create", "pipeline.version"]


def test_pipeline_names_and_committed_sources_are_unique() -> None:
    store = _store()
    request = PublicId.generate(ResourceKind.REQUEST)
    service = PipelineService(store, SourceReader(b"pipeline"))
    definition = service.create_definition(
        store.actor, store.project, "training", request, NOW
    )
    service.register_version(
        store.actor, definition.id, store.repository, "a" * 40, "dvc.yaml", request, NOW
    )

    with pytest.raises(ResourceConflict):
        service.create_definition(store.actor, store.project, "Training", request, NOW)
    with pytest.raises(ResourceConflict):
        service.register_version(
            store.actor, definition.id, store.repository, "a" * 40, "dvc.yaml", request, NOW
        )


def test_only_maintainer_can_archive_pipeline_records() -> None:
    store = _store()
    request = PublicId.generate(ResourceKind.REQUEST)
    definition = PipelineService(store).create_definition(
        store.actor, store.project, "training", request, NOW
    )

    with pytest.raises(AuthorizationDenied):
        PipelineService(store).archive_definition(store.actor, definition.id, request, NOW)

    store.role = ProjectRole.MAINTAINER
    PipelineService(store).archive_definition(store.actor, definition.id, request, NOW)
    assert store.definitions_by_id[definition.id].archived_at == NOW
