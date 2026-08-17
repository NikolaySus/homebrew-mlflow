from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from homebrew_mlflow.domain import (
    AuditEvent,
    OrganizationMembership,
    OrganizationRole,
    Principal,
    PrincipalKind,
    PublicId,
)

from .projects import AuthorizationDenied, ResourceConflict


@dataclass(frozen=True, slots=True)
class OrganizationPrincipalView:
    principal: Principal
    gitlab_username: str | None
    role: OrganizationRole | None
    membership_created_at: datetime | None


class OrganizationMembershipUnitOfWork(Protocol):
    def organization_role(
        self, organization_id: PublicId, principal_id: PublicId
    ) -> OrganizationRole | None: ...

    def principal(self, principal_id: PublicId) -> Principal | None: ...

    def principals(self, organization_id: PublicId) -> tuple[OrganizationPrincipalView, ...]: ...

    def admin_count(self, organization_id: PublicId) -> int: ...

    def put_organization_membership(self, membership: OrganizationMembership) -> None: ...

    def remove_organization_membership(
        self, organization_id: PublicId, principal_id: PublicId
    ) -> None: ...

    def append_audit(self, event: AuditEvent) -> None: ...

    def commit(self) -> None: ...


class OrganizationMembershipService:
    def __init__(self, unit_of_work: OrganizationMembershipUnitOfWork) -> None:
        self._uow = unit_of_work

    def list(
        self, actor_id: PublicId, organization_id: PublicId
    ) -> tuple[OrganizationPrincipalView, ...]:
        self._require_admin(actor_id, organization_id)
        return self._uow.principals(organization_id)

    def set(
        self,
        actor_id: PublicId,
        organization_id: PublicId,
        principal_id: PublicId,
        role: OrganizationRole,
        request_id: PublicId,
        now: datetime,
    ) -> OrganizationPrincipalView:
        self._require_admin(actor_id, organization_id)
        principal = self._uow.principal(principal_id)
        if principal is None:
            raise ValueError("principal does not exist")
        existing = self._uow.organization_role(organization_id, principal_id)
        if principal.kind is PrincipalKind.MACHINE and role is OrganizationRole.ADMIN:
            raise ValueError("machine principals cannot be organization Admins")
        if (
            existing is OrganizationRole.ADMIN
            and role is not OrganizationRole.ADMIN
            and self._uow.admin_count(organization_id) == 1
        ):
            raise ResourceConflict("the last organization Admin cannot be demoted")
        self._uow.put_organization_membership(
            OrganizationMembership(organization_id, principal_id, role, now)
        )
        self._audit(actor_id, organization_id, principal_id, "set", role.value, request_id, now)
        self._uow.commit()
        return next(
            value
            for value in self._uow.principals(organization_id)
            if value.principal.id == principal_id
        )

    def remove(
        self,
        actor_id: PublicId,
        organization_id: PublicId,
        principal_id: PublicId,
        request_id: PublicId,
        now: datetime,
    ) -> None:
        self._require_admin(actor_id, organization_id)
        existing = self._uow.organization_role(organization_id, principal_id)
        if existing is None:
            return
        if existing is OrganizationRole.ADMIN and self._uow.admin_count(organization_id) == 1:
            raise ResourceConflict("the last organization Admin cannot be removed")
        self._uow.remove_organization_membership(organization_id, principal_id)
        self._audit(actor_id, organization_id, principal_id, "remove", None, request_id, now)
        self._uow.commit()

    def _require_admin(self, actor_id: PublicId, organization_id: PublicId) -> None:
        if self._uow.organization_role(organization_id, actor_id) is not OrganizationRole.ADMIN:
            raise AuthorizationDenied("organization Admin role is required")

    def _audit(
        self,
        actor_id: PublicId,
        organization_id: PublicId,
        principal_id: PublicId,
        operation: str,
        role: str | None,
        request_id: PublicId,
        now: datetime,
    ) -> None:
        self._uow.append_audit(
            AuditEvent(
                actor_principal_id=actor_id,
                action=f"organization_membership.{operation}",
                resource_type="organization_membership",
                resource_id=principal_id,
                outcome="success",
                request_id=request_id,
                safe_metadata={
                    "organization_id": str(organization_id),
                    **({"role": role} if role is not None else {}),
                },
                occurred_at=now,
            )
        )
