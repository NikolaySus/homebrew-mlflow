from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

import pytest
from homebrew_mlflow.application import AuthorizationDenied, EnvironmentService, ResourceConflict
from homebrew_mlflow.domain import (
    AuditEvent,
    EnvironmentKind,
    EnvironmentSpecification,
    ProjectRole,
    PublicId,
    ResourceKind,
)

NOW = datetime(2026, 8, 17, 12, tzinfo=UTC)


@dataclass
class EnvironmentStore:
    actor: PublicId
    project: PublicId
    role: ProjectRole | None
    values: dict[PublicId, EnvironmentSpecification] = field(default_factory=dict)
    audits: list[AuditEvent] = field(default_factory=list)

    def project_role(self, project_id: PublicId, principal_id: PublicId) -> ProjectRole | None:
        return self.role if (project_id, principal_id) == (self.project, self.actor) else None

    def name_exists(self, _project_id: PublicId, name: str) -> bool:
        return any(value.name.lower() == name.lower() for value in self.values.values())

    def specifications(
        self, _project_id: PublicId, *, include_archived: bool
    ) -> tuple[EnvironmentSpecification, ...]:
        return tuple(
            value
            for value in self.values.values()
            if include_archived or value.archived_at is None
        )

    def specification(self, specification_id: PublicId) -> EnvironmentSpecification | None:
        return self.values.get(specification_id)

    def add(self, specification: EnvironmentSpecification) -> None:
        self.values[specification.id] = specification

    def archive(self, specification_id: PublicId, at: datetime) -> None:
        self.values[specification_id] = replace(self.values[specification_id], archived_at=at)

    def append_audit(self, event: AuditEvent) -> None:
        self.audits.append(event)

    def commit(self) -> None:
        return None


def _store(role: ProjectRole = ProjectRole.CONTRIBUTOR) -> EnvironmentStore:
    return EnvironmentStore(
        PublicId.generate(ResourceKind.PRINCIPAL),
        PublicId.generate(ResourceKind.PROJECT),
        role,
    )


def test_environment_document_is_canonical_hashed_and_audited() -> None:
    store = _store()
    value = EnvironmentService(store).create(
        store.actor,
        store.project,
        "training",
        EnvironmentKind.UV,
        {"python": "3.12", "lockfile": "uv.lock", "lock_sha256": "a" * 64},
        PublicId.generate(ResourceKind.REQUEST),
        NOW,
    )

    assert value.canonical_document == (
        '{"lock_sha256":"' + "a" * 64 + '","lockfile":"uv.lock","python":"3.12"}'
    )
    assert len(value.sha256) == 64
    assert store.audits[0].safe_metadata == {"kind": "uv", "sha256": value.sha256}


def test_environment_rejects_secrets_and_duplicate_names() -> None:
    store = _store()
    service = EnvironmentService(store)
    request = PublicId.generate(ResourceKind.REQUEST)
    service.create(
        store.actor,
        store.project,
        "training",
        EnvironmentKind.CONDA,
        {"python": "3.12"},
        request,
        NOW,
    )

    with pytest.raises(ResourceConflict):
        service.create(
            store.actor, store.project, "Training", EnvironmentKind.CONDA, {}, request, NOW
        )
    with pytest.raises(ValueError, match="sensitive"):
        service.create(
            store.actor,
            store.project,
            "unsafe",
            EnvironmentKind.SYSTEM,
            {"api_token": "plaintext"},
            request,
            NOW,
        )


def test_only_maintainer_can_archive_environment() -> None:
    store = _store()
    service = EnvironmentService(store)
    request = PublicId.generate(ResourceKind.REQUEST)
    value = service.create(
        store.actor, store.project, "system", EnvironmentKind.SYSTEM, {"os": "linux"}, request, NOW
    )
    with pytest.raises(AuthorizationDenied):
        service.archive(store.actor, value.id, request, NOW)
    store.role = ProjectRole.MAINTAINER
    assert service.archive(store.actor, value.id, request, NOW).archived_at == NOW
