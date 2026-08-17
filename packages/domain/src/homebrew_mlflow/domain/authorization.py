from enum import StrEnum


class OrganizationRole(StrEnum):
    ADMIN = "admin"
    MEMBER = "member"


class ProjectRole(StrEnum):
    VIEWER = "viewer"
    CONTRIBUTOR = "contributor"
    MAINTAINER = "maintainer"


class MachineScope(StrEnum):
    READ = "read"
    TRACK = "track"
    DVC_TRANSFER = "dvc_transfer"
    PUBLISH = "publish"


_MINIMUM_ROLE = {
    MachineScope.READ: ProjectRole.VIEWER,
    MachineScope.TRACK: ProjectRole.CONTRIBUTOR,
    MachineScope.DVC_TRANSFER: ProjectRole.CONTRIBUTOR,
    MachineScope.PUBLISH: ProjectRole.CONTRIBUTOR,
}
_ROLE_RANK = {
    ProjectRole.VIEWER: 10,
    ProjectRole.CONTRIBUTOR: 20,
    ProjectRole.MAINTAINER: 30,
}


def permits(
    project_role: ProjectRole,
    requirement: MachineScope,
    machine_scopes: frozenset[MachineScope] | None = None,
) -> bool:
    """Apply project role ∩ optional machine scopes ∩ endpoint requirement."""
    if _ROLE_RANK[project_role] < _ROLE_RANK[_MINIMUM_ROLE[requirement]]:
        return False
    return machine_scopes is None or requirement in machine_scopes
