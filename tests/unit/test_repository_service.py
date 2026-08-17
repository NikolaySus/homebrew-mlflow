from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest
from homebrew_mlflow.application import (
    AuthorizationDenied,
    CreateRepository,
    RepositoryService,
    ResourceConflict,
)
from homebrew_mlflow.domain import AuditEvent, GitRepository, ProjectRole, PublicId, ResourceKind


@dataclass
class MemoryRepositoryUnitOfWork:
    roles: dict[tuple[PublicId, PublicId], ProjectRole]
    values: list[GitRepository] = field(default_factory=list)
    audits: list[AuditEvent] = field(default_factory=list)
    committed: bool = False

    def project_role(self, project_id: PublicId, principal_id: PublicId) -> ProjectRole | None:
        return self.roles.get((project_id, principal_id))

    def repository_slug_exists(self, project_id: PublicId, slug: str) -> bool:
        return any(
            item.project_id == project_id and item.slug == slug for item in self.values
        )

    def add_repository(self, repository: GitRepository) -> None:
        self.values.append(repository)

    def repositories(self, project_id: PublicId) -> tuple[GitRepository, ...]:
        return tuple(item for item in self.values if item.project_id == project_id)

    def repository(self, repository_id: PublicId) -> GitRepository | None:
        return next((item for item in self.values if item.id == repository_id), None)

    def save_repository(self, repository: GitRepository) -> None:
        self.values = [repository if item.id == repository.id else item for item in self.values]

    def retry_provisioning(self, repository: GitRepository) -> None:
        self.save_repository(repository)

    def append_audit(self, event: AuditEvent) -> None:
        self.audits.append(event)

    def commit(self) -> None:
        self.committed = True


def test_maintainer_creates_durable_pending_repository() -> None:
    project_id = PublicId.generate(ResourceKind.PROJECT)
    actor_id = PublicId.generate(ResourceKind.PRINCIPAL)
    uow = MemoryRepositoryUnitOfWork({(project_id, actor_id): ProjectRole.MAINTAINER})

    repository = RepositoryService(uow).create(
        actor_id,
        CreateRepository(project_id, "Models", "Models", datetime.now(UTC)),
    )

    assert repository.slug == "models"
    assert repository in uow.values
    assert uow.committed


def test_contributor_cannot_create_repository() -> None:
    project_id = PublicId.generate(ResourceKind.PROJECT)
    actor_id = PublicId.generate(ResourceKind.PRINCIPAL)
    uow = MemoryRepositoryUnitOfWork({(project_id, actor_id): ProjectRole.CONTRIBUTOR})

    with pytest.raises(AuthorizationDenied):
        RepositoryService(uow).create(
            actor_id,
            CreateRepository(project_id, "Models", "models", datetime.now(UTC)),
        )


def test_duplicate_repository_slug_is_conflict() -> None:
    project_id = PublicId.generate(ResourceKind.PROJECT)
    actor_id = PublicId.generate(ResourceKind.PRINCIPAL)
    uow = MemoryRepositoryUnitOfWork({(project_id, actor_id): ProjectRole.MAINTAINER})
    service = RepositoryService(uow)
    command = CreateRepository(project_id, "Models", "models", datetime.now(UTC))

    service.create(actor_id, command)
    with pytest.raises(ResourceConflict):
        service.create(actor_id, command)


def test_maintainer_archives_repository_record_with_audit() -> None:
    project_id = PublicId.generate(ResourceKind.PROJECT)
    actor_id = PublicId.generate(ResourceKind.PRINCIPAL)
    uow = MemoryRepositoryUnitOfWork({(project_id, actor_id): ProjectRole.MAINTAINER})
    repository = RepositoryService(uow).create(
        actor_id,
        CreateRepository(project_id, "Models", "models", datetime.now(UTC)),
    ).activate(
        provider_id="42",
        web_url="https://git.example/models",
        http_clone_url="https://git.example/models.git",
        ssh_clone_url="git@git.example:models.git",
        default_branch="main",
    )
    uow.save_repository(repository)

    archived = RepositoryService(uow).archive(
        actor_id,
        project_id,
        repository.id,
        PublicId.generate(ResourceKind.REQUEST),
        datetime.now(UTC),
    )

    assert archived.state.value == "archived"
    assert uow.audits[-1].action == "repository.archive"


def test_maintainer_retries_failed_repository_with_audit() -> None:
    project_id = PublicId.generate(ResourceKind.PROJECT)
    actor_id = PublicId.generate(ResourceKind.PRINCIPAL)
    uow = MemoryRepositoryUnitOfWork({(project_id, actor_id): ProjectRole.MAINTAINER})
    repository = GitRepository.provisioning(
        project_id, "Models", "models", "main", datetime.now(UTC)
    ).fail("template_commit_failed", provider_id="42")
    uow.values.append(repository)

    retried = RepositoryService(uow).retry_provisioning(
        actor_id,
        project_id,
        repository.id,
        PublicId.generate(ResourceKind.REQUEST),
        datetime.now(UTC),
    )

    assert retried.state.value == "provisioning"
    assert retried.provider_id == "42"
    assert retried.failure_code is None
    assert uow.audits[-1].action == "repository.provisioning_retry"
