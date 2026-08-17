from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

import pytest
from homebrew_mlflow.application import (
    AuthorizationDenied,
    CreateProject,
    ProjectService,
    ResourceConflict,
)
from homebrew_mlflow.domain import (
    AuditEvent,
    Organization,
    OrganizationRole,
    Principal,
    PrincipalKind,
    ProjectMembership,
    ProjectRole,
    ProjectState,
    PublicId,
    ResearchProject,
    ResourceKind,
)


@dataclass
class MemoryUnitOfWork:
    roles: dict[tuple[PublicId, PublicId], OrganizationRole]
    principals: dict[PublicId, Principal]
    projects: list[ResearchProject] = field(default_factory=list)
    memberships: list[ProjectMembership] = field(default_factory=list)
    audit: list[AuditEvent] = field(default_factory=list)
    committed: bool = False

    def organization_role(
        self, organization_id: PublicId, principal_id: PublicId
    ) -> OrganizationRole | None:
        return self.roles.get((organization_id, principal_id))

    def principal(self, principal_id: PublicId) -> Principal | None:
        return self.principals.get(principal_id)

    def project_slug_exists(self, organization_id: PublicId, slug: str) -> bool:
        return any(p.organization_id == organization_id and p.slug == slug for p in self.projects)

    def add_project(self, project: ResearchProject) -> None:
        self.projects.append(project)

    def add_membership(self, membership: ProjectMembership) -> None:
        self.memberships.append(membership)

    def append_audit(self, event: AuditEvent) -> None:
        self.audit.append(event)

    def commit(self) -> None:
        self.committed = True

    def projects_for_principal(self, principal_id: PublicId) -> tuple[ResearchProject, ...]:
        project_ids = {
            membership.project_id
            for membership in self.memberships
            if membership.principal_id == principal_id
        }
        return tuple(project for project in self.projects if project.id in project_ids)

    def project(self, project_id: PublicId) -> ResearchProject | None:
        return next((project for project in self.projects if project.id == project_id), None)

    def project_role(self, project_id: PublicId, principal_id: PublicId) -> ProjectRole | None:
        membership = next(
            (
                value
                for value in self.memberships
                if (value.project_id, value.principal_id) == (project_id, principal_id)
            ),
            None,
        )
        return membership.role if membership is not None else None

    def set_project_archived(
        self, project_id: PublicId, archived_at: datetime | None
    ) -> None:
        self.projects = [
            replace(
                project,
                archived_at=archived_at,
                state=ProjectState.ARCHIVED if archived_at else ProjectState.ACTIVE,
            )
            if project.id == project_id
            else project
            for project in self.projects
        ]


def fixture() -> tuple[Organization, Principal, Principal, MemoryUnitOfWork]:
    organization = Organization.create("Research")
    admin = Principal.create(PrincipalKind.HUMAN, "Admin")
    maintainer = Principal.create(PrincipalKind.HUMAN, "Maintainer")
    roles = {
        (organization.id, admin.id): OrganizationRole.ADMIN,
        (organization.id, maintainer.id): OrganizationRole.MEMBER,
    }
    return (
        organization,
        admin,
        maintainer,
        MemoryUnitOfWork(roles, {admin.id: admin, maintainer.id: maintainer}),
    )


def command(organization: Organization, maintainer: Principal) -> CreateProject:
    return CreateProject(
        organization_id=organization.id,
        name="Protein Folding",
        slug="protein-folding",
        initial_maintainer_id=maintainer.id,
        request_id=PublicId.generate(ResourceKind.REQUEST),
    )


def test_admin_creates_project_with_explicit_maintainer_and_audit() -> None:
    organization, admin, maintainer, uow = fixture()
    project = ProjectService(uow).create(admin.id, command(organization, maintainer))
    assert project in uow.projects
    assert uow.memberships[0].principal_id == maintainer.id
    assert uow.audit[0].action == "project.create"
    assert uow.committed


def test_ordinary_member_cannot_create_project() -> None:
    organization, _admin, member, uow = fixture()
    with pytest.raises(AuthorizationDenied):
        ProjectService(uow).create(member.id, command(organization, member))


def test_duplicate_project_slug_is_conflict() -> None:
    organization, admin, maintainer, uow = fixture()
    service = ProjectService(uow)
    service.create(admin.id, command(organization, maintainer))
    with pytest.raises(ResourceConflict):
        service.create(admin.id, command(organization, maintainer))


def test_maintainer_archives_and_organization_admin_restores_project() -> None:
    organization, admin, maintainer, uow = fixture()
    service = ProjectService(uow)
    project = service.create(admin.id, command(organization, maintainer))
    uow.projects[0] = replace(project, state=ProjectState.ACTIVE)
    request = PublicId.generate(ResourceKind.REQUEST)
    now = datetime(2026, 8, 17, 12, tzinfo=UTC)

    archived = service.archive(maintainer.id, project.id, request, now)
    restored = service.restore(admin.id, project.id, request, now)

    assert archived.state is ProjectState.ARCHIVED
    assert archived.archived_at == now
    assert restored.state is ProjectState.ACTIVE
    assert restored.archived_at is None
    assert [event.action for event in uow.audit[-2:]] == ["project.archive", "project.restore"]
