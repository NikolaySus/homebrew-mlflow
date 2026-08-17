from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum

from .identifiers import PublicId, ResourceKind


class RepositoryState(StrEnum):
    PROVISIONING = "provisioning"
    ACTIVE = "active"
    FAILED = "failed"
    ARCHIVED = "archived"


class InvalidRepositoryTransition(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class GitRepository:
    id: PublicId
    project_id: PublicId
    name: str
    slug: str
    default_branch: str
    state: RepositoryState
    created_at: datetime
    provider_id: str | None = None
    web_url: str | None = None
    http_clone_url: str | None = None
    ssh_clone_url: str | None = None
    failure_code: str | None = None

    def __post_init__(self) -> None:
        if self.id.kind is not ResourceKind.REPOSITORY:
            raise ValueError("invalid Repository identifier")
        if self.project_id.kind is not ResourceKind.PROJECT:
            raise ValueError("repository must belong to a Research Project")
        if not self.name.strip() or not self.slug.strip() or not self.default_branch.strip():
            raise ValueError("repository name, slug, and default branch are required")
        if not self.slug.replace("-", "").isalnum():
            raise ValueError("repository slug may contain only letters, digits, and hyphens")
        if self.state is RepositoryState.ACTIVE and not all(
            (self.provider_id, self.web_url, self.http_clone_url, self.ssh_clone_url)
        ):
            raise ValueError("active repository requires complete provider coordinates")
        if self.state is RepositoryState.FAILED and not self.failure_code:
            raise ValueError("failed repository requires a failure code")

    @classmethod
    def provisioning(
        cls,
        project_id: PublicId,
        name: str,
        slug: str,
        default_branch: str,
        created_at: datetime,
    ) -> GitRepository:
        return cls(
            id=PublicId.generate(ResourceKind.REPOSITORY),
            project_id=project_id,
            name=name.strip(),
            slug=slug.strip().lower(),
            default_branch=default_branch.strip(),
            state=RepositoryState.PROVISIONING,
            created_at=created_at,
        )

    def activate(
        self,
        *,
        provider_id: str,
        web_url: str,
        http_clone_url: str,
        ssh_clone_url: str,
        default_branch: str,
    ) -> GitRepository:
        if self.state not in {RepositoryState.PROVISIONING, RepositoryState.FAILED}:
            raise InvalidRepositoryTransition(f"cannot activate repository from {self.state}")
        return replace(
            self,
            state=RepositoryState.ACTIVE,
            provider_id=provider_id,
            web_url=web_url,
            http_clone_url=http_clone_url,
            ssh_clone_url=ssh_clone_url,
            default_branch=default_branch,
            failure_code=None,
        )

    def fail(self, code: str, *, provider_id: str | None = None) -> GitRepository:
        if self.state is not RepositoryState.PROVISIONING:
            raise InvalidRepositoryTransition(f"cannot fail repository from {self.state}")
        if not code.strip():
            raise ValueError("repository failure code is required")
        return replace(
            self,
            state=RepositoryState.FAILED,
            failure_code=code.strip(),
            provider_id=provider_id or self.provider_id,
        )

    def archive(self) -> GitRepository:
        if self.state is RepositoryState.ARCHIVED:
            return self
        if self.state not in {RepositoryState.ACTIVE, RepositoryState.FAILED}:
            raise InvalidRepositoryTransition(f"cannot archive repository from {self.state}")
        return replace(self, state=RepositoryState.ARCHIVED)
