from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from homebrew_mlflow.domain import (
    AuditEvent,
    OrganizationRole,
    Principal,
    ProjectMembership,
    ProjectRole,
    ProjectState,
    PublicId,
    ResearchProject,
)


class AuthorizationDenied(PermissionError):
    pass


class ResourceConflict(ValueError):
    pass


class ProjectUnitOfWork(Protocol):
    def organization_role(
        self, organization_id: PublicId, principal_id: PublicId
    ) -> OrganizationRole | None: ...

    def principal(self, principal_id: PublicId) -> Principal | None: ...

    def project_slug_exists(self, organization_id: PublicId, slug: str) -> bool: ...

    def add_project(self, project: ResearchProject) -> None: ...

    def add_membership(self, membership: ProjectMembership) -> None: ...

    def append_audit(self, event: AuditEvent) -> None: ...

    def commit(self) -> None: ...

    def projects_for_principal(self, principal_id: PublicId) -> tuple[ResearchProject, ...]: ...

    def project(self, project_id: PublicId) -> ResearchProject | None: ...

    def project_role(self, project_id: PublicId, principal_id: PublicId) -> ProjectRole | None: ...

    def set_project_archived(self, project_id: PublicId, archived_at: datetime | None) -> None: ...


@dataclass(frozen=True, slots=True)
class CreateProject:
    organization_id: PublicId
    name: str
    slug: str
    initial_maintainer_id: PublicId
    request_id: PublicId


class ProjectService:
    def __init__(self, unit_of_work: ProjectUnitOfWork) -> None:
        self._uow = unit_of_work

    def create(self, actor_id: PublicId, command: CreateProject) -> ResearchProject:
        if (
            self._uow.organization_role(command.organization_id, actor_id)
            is not OrganizationRole.ADMIN
        ):
            raise AuthorizationDenied("organization Admin role is required")
        maintainer = self._uow.principal(command.initial_maintainer_id)
        if maintainer is None:
            raise ValueError("initial Maintainer principal does not exist")
        maintainer_role = self._uow.organization_role(command.organization_id, maintainer.id)
        if maintainer_role is None:
            raise ValueError("initial Maintainer must belong to the organization")
        if self._uow.project_slug_exists(command.organization_id, command.slug.lower()):
            raise ResourceConflict("project slug already exists")

        project = ResearchProject.create(command.organization_id, command.name, command.slug)
        membership = ProjectMembership.create(
            project.id,
            maintainer,
            ProjectRole.MAINTAINER,
            belongs_to_organization=True,
        )
        self._uow.add_project(project)
        self._uow.add_membership(membership)
        self._uow.append_audit(
            AuditEvent(
                actor_principal_id=actor_id,
                action="project.create",
                resource_type="research_project",
                resource_id=project.id,
                outcome="success",
                request_id=command.request_id,
                project_id=project.id,
                safe_metadata={"initial_maintainer_id": str(maintainer.id)},
            )
        )
        self._uow.commit()
        return project

    def list_for_actor(self, actor_id: PublicId) -> tuple[ResearchProject, ...]:
        return self._uow.projects_for_principal(actor_id)

    def archive(
        self, actor_id: PublicId, project_id: PublicId, request_id: PublicId, now: datetime
    ) -> ResearchProject:
        project = self._required_project(project_id)
        if self._uow.project_role(project_id, actor_id) is not ProjectRole.MAINTAINER:
            raise AuthorizationDenied("project Maintainer role is required")
        if project.state is ProjectState.ARCHIVED:
            return project
        if project.state is not ProjectState.ACTIVE:
            raise ResourceConflict("only an active Research Project can be archived")
        self._uow.set_project_archived(project_id, now)
        self._append_lifecycle_audit(actor_id, project, "archive", request_id, now)
        self._uow.commit()
        return self._required_project(project_id)

    def restore(
        self, actor_id: PublicId, project_id: PublicId, request_id: PublicId, now: datetime
    ) -> ResearchProject:
        project = self._required_project(project_id)
        if (
            self._uow.organization_role(project.organization_id, actor_id)
            is not OrganizationRole.ADMIN
        ):
            raise AuthorizationDenied("organization Admin role is required")
        if project.state is not ProjectState.ARCHIVED:
            raise ResourceConflict("only an archived Research Project can be restored")
        self._uow.set_project_archived(project_id, None)
        self._append_lifecycle_audit(actor_id, project, "restore", request_id, now)
        self._uow.commit()
        return self._required_project(project_id)

    def _required_project(self, project_id: PublicId) -> ResearchProject:
        project = self._uow.project(project_id)
        if project is None:
            raise ValueError("Research Project does not exist")
        return project

    def _append_lifecycle_audit(
        self,
        actor_id: PublicId,
        project: ResearchProject,
        action: str,
        request_id: PublicId,
        now: datetime,
    ) -> None:
        self._uow.append_audit(
            AuditEvent(
                actor_principal_id=actor_id,
                action=f"project.{action}",
                resource_type="research_project",
                resource_id=project.id,
                outcome="success",
                request_id=request_id,
                project_id=project.id,
                safe_metadata={"organization_id": str(project.organization_id)},
                occurred_at=now,
            )
        )
