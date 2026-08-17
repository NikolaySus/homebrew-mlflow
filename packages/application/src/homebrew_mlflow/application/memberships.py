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
    PublicId,
)

from .projects import AuthorizationDenied, ResourceConflict


@dataclass(frozen=True, slots=True)
class ProjectMembershipView:
    membership: ProjectMembership
    principal: Principal
    gitlab_username: str | None


class MembershipUnitOfWork(Protocol):
    def project_role(self, project_id: PublicId, principal_id: PublicId) -> ProjectRole | None: ...

    def project_organization(self, project_id: PublicId) -> PublicId | None: ...

    def organization_role(
        self, organization_id: PublicId, principal_id: PublicId
    ) -> OrganizationRole | None: ...

    def principal(self, principal_id: PublicId) -> Principal | None: ...

    def belongs_to_organization(
        self, organization_id: PublicId, principal_id: PublicId
    ) -> bool: ...

    def membership(
        self, project_id: PublicId, principal_id: PublicId
    ) -> ProjectMembership | None: ...

    def memberships(self, project_id: PublicId) -> tuple[ProjectMembershipView, ...]: ...

    def maintainer_count(self, project_id: PublicId) -> int: ...

    def put_membership(self, membership: ProjectMembership) -> None: ...

    def remove_membership(self, project_id: PublicId, principal_id: PublicId) -> None: ...

    def mark_reconciliation_pending(self, project_id: PublicId, changed_at: datetime) -> None: ...

    def append_audit(self, event: AuditEvent) -> None: ...

    def commit(self) -> None: ...


class ProjectMembershipService:
    def __init__(self, unit_of_work: MembershipUnitOfWork) -> None:
        self._uow = unit_of_work

    def list(self, actor_id: PublicId, project_id: PublicId) -> tuple[ProjectMembershipView, ...]:
        if self._uow.project_role(project_id, actor_id) is None:
            raise AuthorizationDenied("project membership is required")
        return self._uow.memberships(project_id)

    def set(
        self,
        actor_id: PublicId,
        project_id: PublicId,
        principal_id: PublicId,
        role: ProjectRole,
        request_id: PublicId,
        now: datetime,
    ) -> ProjectMembershipView:
        self._require_maintainer(actor_id, project_id)
        organization_id = self._uow.project_organization(project_id)
        principal = self._uow.principal(principal_id)
        if organization_id is None or principal is None:
            raise ValueError("project or principal does not exist")
        belongs = self._uow.belongs_to_organization(organization_id, principal_id)
        existing = self._uow.membership(project_id, principal_id)
        if (
            existing is not None
            and existing.role is ProjectRole.MAINTAINER
            and role is not ProjectRole.MAINTAINER
            and self._uow.maintainer_count(project_id) == 1
        ):
            raise ResourceConflict("the last project Maintainer cannot be demoted")
        created = ProjectMembership.create(
            project_id, principal, role, belongs_to_organization=belongs
        )
        membership = ProjectMembership(
            created.project_id,
            created.principal_id,
            created.role,
            existing.created_at if existing is not None else now,
        )
        self._uow.put_membership(membership)
        self._uow.mark_reconciliation_pending(project_id, now)
        self._audit(actor_id, project_id, principal_id, role.value, request_id, now, "set")
        self._uow.commit()
        return next(
            item for item in self._uow.memberships(project_id) if item.principal.id == principal_id
        )

    def remove(
        self,
        actor_id: PublicId,
        project_id: PublicId,
        principal_id: PublicId,
        request_id: PublicId,
        now: datetime,
    ) -> None:
        self._require_maintainer(actor_id, project_id)
        existing = self._uow.membership(project_id, principal_id)
        if existing is None:
            return
        if (
            existing.role is ProjectRole.MAINTAINER
            and self._uow.maintainer_count(project_id) == 1
        ):
            raise ResourceConflict("the last project Maintainer cannot be removed")
        self._uow.remove_membership(project_id, principal_id)
        self._uow.mark_reconciliation_pending(project_id, now)
        self._audit(actor_id, project_id, principal_id, None, request_id, now, "remove")
        self._uow.commit()

    def recover_maintainer(
        self,
        actor_id: PublicId,
        project_id: PublicId,
        principal_id: PublicId,
        request_id: PublicId,
        now: datetime,
    ) -> ProjectMembershipView:
        organization_id = self._uow.project_organization(project_id)
        if organization_id is None:
            raise ValueError("project does not exist")
        if self._uow.organization_role(organization_id, actor_id) is not OrganizationRole.ADMIN:
            raise AuthorizationDenied("organization Admin role is required for recovery")
        principal = self._uow.principal(principal_id)
        if principal is None:
            raise ValueError("principal does not exist")
        created = ProjectMembership.create(
            project_id,
            principal,
            ProjectRole.MAINTAINER,
            belongs_to_organization=self._uow.belongs_to_organization(
                organization_id, principal_id
            ),
        )
        existing = self._uow.membership(project_id, principal_id)
        membership = ProjectMembership(
            created.project_id,
            created.principal_id,
            created.role,
            existing.created_at if existing is not None else now,
        )
        self._uow.put_membership(membership)
        self._uow.mark_reconciliation_pending(project_id, now)
        self._audit(actor_id, project_id, principal_id, "maintainer", request_id, now, "recover")
        self._uow.commit()
        return next(
            item for item in self._uow.memberships(project_id) if item.principal.id == principal_id
        )

    def _require_maintainer(self, actor_id: PublicId, project_id: PublicId) -> None:
        if self._uow.project_role(project_id, actor_id) is not ProjectRole.MAINTAINER:
            raise AuthorizationDenied("Maintainer role is required to manage membership")

    def _audit(
        self,
        actor_id: PublicId,
        project_id: PublicId,
        principal_id: PublicId,
        role: str | None,
        request_id: PublicId,
        now: datetime,
        operation: str,
    ) -> None:
        self._uow.append_audit(
            AuditEvent(
                actor_principal_id=actor_id,
                action=f"project_membership.{operation}",
                resource_type="project_membership",
                resource_id=principal_id,
                outcome="success",
                request_id=request_id,
                project_id=project_id,
                safe_metadata={"role": role} if role is not None else {},
                occurred_at=now,
            )
        )
