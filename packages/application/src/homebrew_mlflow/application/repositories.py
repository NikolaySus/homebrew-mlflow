from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath
from typing import Protocol

from homebrew_mlflow.domain import AuditEvent, GitRepository, ProjectRole, PublicId, ResourceKind

from .projects import AuthorizationDenied, ResourceConflict


@dataclass(frozen=True, slots=True)
class RepositorySeedFile:
    path: str
    content: str
    executable: bool = False

    def __post_init__(self) -> None:
        candidate = PurePosixPath(self.path)
        if not self.path or candidate.is_absolute() or ".." in candidate.parts or "\\" in self.path:
            raise ValueError("repository seed path must be a safe relative POSIX path")


@dataclass(frozen=True, slots=True)
class RepositoryTemplateContext:
    repository_id: PublicId
    project_id: PublicId
    project_name: str
    repository_name: str
    repository_slug: str
    platform_url: str
    dvc_remote_url: str
    s3_endpoint_url: str


class RepositoryTemplate(Protocol):
    def render(self, context: RepositoryTemplateContext) -> tuple[RepositorySeedFile, ...]: ...


@dataclass(frozen=True, slots=True)
class HostedRepositoryRequest:
    namespace_id: int
    name: str
    slug: str
    default_branch: str


@dataclass(frozen=True, slots=True)
class HostedRepository:
    provider_id: str
    default_branch: str
    web_url: str
    http_clone_url: str
    ssh_clone_url: str


class GitRepositoryHost(Protocol):
    def create_with_seed(
        self,
        request: HostedRepositoryRequest,
        files: tuple[RepositorySeedFile, ...],
    ) -> HostedRepository: ...


class RepositoryUnitOfWork(Protocol):
    def project_role(self, project_id: PublicId, principal_id: PublicId) -> ProjectRole | None: ...

    def repository_slug_exists(self, project_id: PublicId, slug: str) -> bool: ...

    def add_repository(self, repository: GitRepository) -> None: ...

    def repositories(self, project_id: PublicId) -> tuple[GitRepository, ...]: ...

    def repository(self, repository_id: PublicId) -> GitRepository | None: ...

    def save_repository(self, repository: GitRepository) -> None: ...

    def append_audit(self, event: AuditEvent) -> None: ...

    def commit(self) -> None: ...


@dataclass(frozen=True, slots=True)
class CreateRepository:
    project_id: PublicId
    name: str
    slug: str
    occurred_at: datetime
    default_branch: str = "main"


class RepositoryService:
    def __init__(self, unit_of_work: RepositoryUnitOfWork) -> None:
        self._uow = unit_of_work

    def create(self, actor_id: PublicId, command: CreateRepository) -> GitRepository:
        if self._uow.project_role(command.project_id, actor_id) is not ProjectRole.MAINTAINER:
            raise AuthorizationDenied("project Maintainer role is required")
        normalized_slug = command.slug.strip().lower()
        if self._uow.repository_slug_exists(command.project_id, normalized_slug):
            raise ResourceConflict("repository slug already exists")
        repository = GitRepository.provisioning(
            command.project_id,
            command.name,
            normalized_slug,
            command.default_branch,
            command.occurred_at,
        )
        self._uow.add_repository(repository)
        self._uow.commit()
        return repository

    def list(self, actor_id: PublicId, project_id: PublicId) -> tuple[GitRepository, ...]:
        if self._uow.project_role(project_id, actor_id) is None:
            raise AuthorizationDenied("project membership is required")
        return self._uow.repositories(project_id)

    def archive(
        self,
        actor_id: PublicId,
        project_id: PublicId,
        repository_id: PublicId,
        request_id: PublicId,
        now: datetime,
    ) -> GitRepository:
        if self._uow.project_role(project_id, actor_id) is not ProjectRole.MAINTAINER:
            raise AuthorizationDenied("project Maintainer role is required")
        repository = self._uow.repository(repository_id)
        if repository is None or repository.project_id != project_id:
            raise ValueError("repository does not exist in the selected project")
        archived = repository.archive()
        self._uow.save_repository(archived)
        self._uow.append_audit(
            AuditEvent(
                actor_principal_id=actor_id,
                action="repository.archive",
                resource_type="git_repository",
                resource_id=repository.id,
                outcome="success",
                request_id=request_id,
                project_id=project_id,
                safe_metadata={"provider_id": repository.provider_id},
                occurred_at=now,
            )
        )
        self._uow.commit()
        return archived


@dataclass(frozen=True, slots=True)
class ProvisionRepository:
    project_id: PublicId
    project_name: str
    namespace_id: int
    repository_name: str
    repository_slug: str
    platform_url: str
    dvc_remote_url: str
    s3_endpoint_url: str
    default_branch: str = "main"


@dataclass(frozen=True, slots=True)
class ProvisionedRepository:
    id: PublicId
    project_id: PublicId
    hosted: HostedRepository
    seeded_paths: tuple[str, ...]


class RepositoryProvisioningService:
    def __init__(self, template: RepositoryTemplate, host: GitRepositoryHost) -> None:
        self._template = template
        self._host = host

    def provision(self, command: ProvisionRepository) -> ProvisionedRepository:
        if command.project_id.kind is not ResourceKind.PROJECT:
            raise ValueError("repository must belong to a Research Project")
        if command.namespace_id <= 0:
            raise ValueError("GitLab namespace ID must be positive")
        if not command.repository_name.strip() or not command.repository_slug.strip():
            raise ValueError("repository name and slug are required")
        if not command.default_branch.strip():
            raise ValueError("default branch is required")

        repository_id = PublicId.generate(ResourceKind.REPOSITORY)
        files = self._template.render(
            RepositoryTemplateContext(
                repository_id=repository_id,
                project_id=command.project_id,
                project_name=command.project_name,
                repository_name=command.repository_name,
                repository_slug=command.repository_slug,
                platform_url=command.platform_url.rstrip("/"),
                dvc_remote_url=command.dvc_remote_url,
                s3_endpoint_url=command.s3_endpoint_url.rstrip("/"),
            )
        )
        if not files:
            raise ValueError("repository template must contain at least one file")

        hosted = self._host.create_with_seed(
            HostedRepositoryRequest(
                namespace_id=command.namespace_id,
                name=command.repository_name.strip(),
                slug=command.repository_slug.strip(),
                default_branch=command.default_branch.strip(),
            ),
            files,
        )
        return ProvisionedRepository(
            id=repository_id,
            project_id=command.project_id,
            hosted=hosted,
            seeded_paths=tuple(file.path for file in files),
        )
