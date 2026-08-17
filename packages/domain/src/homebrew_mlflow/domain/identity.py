from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from .authorization import OrganizationRole, ProjectRole
from .identifiers import PublicId, ResourceKind


def utc_now() -> datetime:
    return datetime.now(UTC)


class PrincipalKind(StrEnum):
    HUMAN = "human"
    MACHINE = "machine"


class ProjectState(StrEnum):
    PROVISIONING = "provisioning"
    ACTIVE = "active"
    FAILED = "failed"
    ARCHIVED = "archived"


class MembershipInvariantError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Organization:
    id: PublicId
    name: str
    created_at: datetime

    @classmethod
    def create(cls, name: str) -> Organization:
        normalized = name.strip()
        if not normalized:
            raise ValueError("organization name is required")
        return cls(PublicId.generate(ResourceKind.ORGANIZATION), normalized, utc_now())


@dataclass(frozen=True, slots=True)
class Principal:
    id: PublicId
    kind: PrincipalKind
    display_name: str
    created_at: datetime

    @classmethod
    def create(cls, kind: PrincipalKind, display_name: str) -> Principal:
        normalized = display_name.strip()
        if not normalized:
            raise ValueError("principal display name is required")
        return cls(PublicId.generate(ResourceKind.PRINCIPAL), kind, normalized, utc_now())


@dataclass(frozen=True, slots=True)
class ResearchProject:
    id: PublicId
    organization_id: PublicId
    name: str
    slug: str
    created_at: datetime
    state: ProjectState = ProjectState.PROVISIONING
    gitlab_namespace_id: str | None = None
    failure_code: str | None = None
    archived_at: datetime | None = None

    @classmethod
    def create(cls, organization_id: PublicId, name: str, slug: str) -> ResearchProject:
        normalized_name = name.strip()
        normalized_slug = slug.strip().lower()
        if organization_id.kind is not ResourceKind.ORGANIZATION:
            raise ValueError("a project must belong to an organization")
        if not normalized_name or not normalized_slug:
            raise ValueError("project name and slug are required")
        if re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", normalized_slug) is None:
            raise ValueError("project slug must be lowercase ASCII letters, digits, and hyphens")
        return cls(
            PublicId.generate(ResourceKind.PROJECT),
            organization_id,
            normalized_name,
            normalized_slug,
            utc_now(),
        )


@dataclass(frozen=True, slots=True)
class OrganizationMembership:
    organization_id: PublicId
    principal_id: PublicId
    role: OrganizationRole
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ProjectMembership:
    project_id: PublicId
    principal_id: PublicId
    role: ProjectRole
    created_at: datetime

    @classmethod
    def create(
        cls,
        project_id: PublicId,
        principal: Principal,
        role: ProjectRole,
        *,
        belongs_to_organization: bool,
    ) -> ProjectMembership:
        if not belongs_to_organization:
            raise MembershipInvariantError(
                "principal must belong to the organization before receiving a project role"
            )
        if principal.kind is PrincipalKind.MACHINE and role is ProjectRole.MAINTAINER:
            raise MembershipInvariantError("machine principals cannot be project Maintainers")
        return cls(project_id, principal.id, role, utc_now())
