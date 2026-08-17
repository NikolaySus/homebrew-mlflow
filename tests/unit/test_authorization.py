from homebrew_mlflow.domain import MachineScope, ProjectRole, permits


def test_role_and_machine_scope_must_both_allow_operation() -> None:
    scopes = frozenset({MachineScope.READ, MachineScope.TRACK})
    assert permits(ProjectRole.CONTRIBUTOR, MachineScope.TRACK, scopes)
    assert not permits(ProjectRole.CONTRIBUTOR, MachineScope.PUBLISH, scopes)
    assert not permits(ProjectRole.VIEWER, MachineScope.TRACK, scopes)


def test_human_authority_is_limited_by_project_role() -> None:
    assert permits(ProjectRole.VIEWER, MachineScope.READ)
    assert not permits(ProjectRole.VIEWER, MachineScope.DVC_TRANSFER)
    assert permits(ProjectRole.MAINTAINER, MachineScope.PUBLISH)
