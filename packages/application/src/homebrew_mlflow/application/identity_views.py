from dataclasses import dataclass
from typing import Protocol

from homebrew_mlflow.domain import Organization, OrganizationRole, Principal, ProjectRole, PublicId


@dataclass(frozen=True, slots=True)
class OrganizationRoleView:
    organization_id: PublicId
    role: OrganizationRole


@dataclass(frozen=True, slots=True)
class ProjectRoleView:
    project_id: PublicId
    role: ProjectRole


@dataclass(frozen=True, slots=True)
class MeView:
    principal: Principal
    organizations: tuple[OrganizationRoleView, ...]
    projects: tuple[ProjectRoleView, ...]


class IdentityReadStore(Protocol):
    def me(self, principal_id: PublicId) -> MeView | None: ...

    def organization_for_principal(self, principal_id: PublicId) -> Organization | None: ...


class IdentityViewService:
    def __init__(self, store: IdentityReadStore) -> None:
        self._store = store

    def me(self, principal_id: PublicId) -> MeView:
        value = self._store.me(principal_id)
        if value is None:
            raise ValueError("Principal does not exist")
        return value

    def organization(self, principal_id: PublicId) -> Organization:
        value = self._store.organization_for_principal(principal_id)
        if value is None:
            raise ValueError("Organization membership does not exist")
        return value
