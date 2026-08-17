import pytest
from homebrew_mlflow.domain import (
    MembershipInvariantError,
    Organization,
    Principal,
    PrincipalKind,
    ProjectMembership,
    ProjectRole,
    ResearchProject,
    ResourceKind,
)


def test_generated_ids_have_expected_kind_and_are_unique() -> None:
    first = Organization.create("Research")
    second = Organization.create("Research")
    assert first.id.kind is ResourceKind.ORGANIZATION
    assert first.id != second.id


def test_project_requires_organization_and_safe_slug() -> None:
    organization = Organization.create("Research")
    project = ResearchProject.create(organization.id, "Protein Folding", "protein-folding")
    assert project.slug == "protein-folding"

    with pytest.raises(ValueError, match="slug"):
        ResearchProject.create(organization.id, "Unsafe", "../unsafe")


def test_machine_cannot_be_maintainer() -> None:
    organization = Organization.create("Research")
    project = ResearchProject.create(organization.id, "Models", "models")
    machine = Principal.create(PrincipalKind.MACHINE, "training host")

    with pytest.raises(MembershipInvariantError, match="Maintainers"):
        ProjectMembership.create(
            project.id,
            machine,
            ProjectRole.MAINTAINER,
            belongs_to_organization=True,
        )


def test_project_membership_requires_organization_membership() -> None:
    organization = Organization.create("Research")
    project = ResearchProject.create(organization.id, "Models", "models")
    human = Principal.create(PrincipalKind.HUMAN, "Researcher")

    with pytest.raises(MembershipInvariantError, match="must belong"):
        ProjectMembership.create(
            project.id,
            human,
            ProjectRole.VIEWER,
            belongs_to_organization=False,
        )
