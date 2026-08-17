from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest
from homebrew_mlflow.application import (
    ProjectMembershipService,
    ProjectMembershipView,
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
    PublicId,
    ResourceKind,
)

NOW = datetime(2026, 8, 17, 12, tzinfo=UTC)


@dataclass
class MembershipStore:
    organization: Organization
    project_id: PublicId
    people: dict[PublicId, Principal]
    values: dict[PublicId, ProjectMembership]
    audits: list[AuditEvent] = field(default_factory=list)
    pending: bool = False
    admins: set[PublicId] = field(default_factory=set)

    def project_role(self, _project_id: PublicId, principal_id: PublicId) -> ProjectRole | None:
        value = self.values.get(principal_id)
        return value.role if value is not None else None

    def project_organization(self, _project_id: PublicId) -> PublicId:
        return self.organization.id

    def organization_role(
        self, _organization_id: PublicId, principal_id: PublicId
    ) -> OrganizationRole | None:
        return OrganizationRole.ADMIN if principal_id in self.admins else None

    def principal(self, principal_id: PublicId) -> Principal | None:
        return self.people.get(principal_id)

    def belongs_to_organization(
        self, _organization_id: PublicId, principal_id: PublicId
    ) -> bool:
        return principal_id in self.people

    def membership(
        self, _project_id: PublicId, principal_id: PublicId
    ) -> ProjectMembership | None:
        return self.values.get(principal_id)

    def memberships(self, _project_id: PublicId) -> tuple[ProjectMembershipView, ...]:
        return tuple(
            ProjectMembershipView(value, self.people[principal_id], None)
            for principal_id, value in self.values.items()
        )

    def maintainer_count(self, _project_id: PublicId) -> int:
        return sum(value.role is ProjectRole.MAINTAINER for value in self.values.values())

    def put_membership(self, membership: ProjectMembership) -> None:
        self.values[membership.principal_id] = membership

    def remove_membership(self, _project_id: PublicId, principal_id: PublicId) -> None:
        self.values.pop(principal_id, None)

    def mark_reconciliation_pending(self, _project_id: PublicId, _changed_at: datetime) -> None:
        self.pending = True

    def append_audit(self, event: AuditEvent) -> None:
        self.audits.append(event)

    def commit(self) -> None:
        return None


def _store() -> tuple[MembershipStore, Principal, Principal]:
    organization = Organization.create("Research")
    project = PublicId.generate(ResourceKind.PROJECT)
    maintainer = Principal.create(PrincipalKind.HUMAN, "Maintainer")
    researcher = Principal.create(PrincipalKind.HUMAN, "Researcher")
    membership = ProjectMembership(project, maintainer.id, ProjectRole.MAINTAINER, NOW)
    return (
        MembershipStore(
            organization,
            project,
            {maintainer.id: maintainer, researcher.id: researcher},
            {maintainer.id: membership},
        ),
        maintainer,
        researcher,
    )


def test_maintainer_adds_member_and_queues_audited_reconciliation() -> None:
    store, maintainer, researcher = _store()
    service = ProjectMembershipService(store)

    result = service.set(
        maintainer.id,
        store.project_id,
        researcher.id,
        ProjectRole.CONTRIBUTOR,
        PublicId.generate(ResourceKind.REQUEST),
        NOW,
    )

    assert result.membership.role is ProjectRole.CONTRIBUTOR
    assert store.pending
    assert store.audits[0].action == "project_membership.set"


def test_last_maintainer_cannot_be_demoted_or_removed() -> None:
    store, maintainer, _researcher = _store()
    service = ProjectMembershipService(store)
    request = PublicId.generate(ResourceKind.REQUEST)

    with pytest.raises(ResourceConflict):
        service.set(
            maintainer.id,
            store.project_id,
            maintainer.id,
            ProjectRole.VIEWER,
            request,
            NOW,
        )
    with pytest.raises(ResourceConflict):
        service.remove(maintainer.id, store.project_id, maintainer.id, request, NOW)


def test_organization_admin_can_recover_project_maintainership() -> None:
    store, maintainer, researcher = _store()
    store.admins.add(maintainer.id)
    result = ProjectMembershipService(store).recover_maintainer(
        maintainer.id,
        store.project_id,
        researcher.id,
        PublicId.generate(ResourceKind.REQUEST),
        NOW,
    )

    assert result.membership.role is ProjectRole.MAINTAINER
    assert store.audits[-1].action == "project_membership.recover"
