from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest
from homebrew_mlflow.application import (
    CreateRun,
    FinalizeRun,
    ResourceConflict,
    RunService,
)
from homebrew_mlflow.domain import (
    AuditEvent,
    Experiment,
    ProjectRole,
    PublicId,
    ResourceKind,
    Run,
    RunProvenanceStatus,
    RunState,
)


@dataclass
class MemoryRunUnitOfWork:
    roles: dict[tuple[PublicId, PublicId], ProjectRole]
    active_repositories: set[tuple[PublicId, PublicId]]
    experiments: list[Experiment] = field(default_factory=list)
    runs: dict[PublicId, Run] = field(default_factory=dict)
    commits: int = 0
    audits: list[AuditEvent] = field(default_factory=list)

    def project_role(self, project_id: PublicId, principal_id: PublicId) -> ProjectRole | None:
        return self.roles.get((project_id, principal_id))

    def repository_belongs_to_project(self, repository_id: PublicId, project_id: PublicId) -> bool:
        return (repository_id, project_id) in self.active_repositories

    def experiment_by_name(self, project_id: PublicId, name: str) -> Experiment | None:
        return next(
            (
                experiment
                for experiment in self.experiments
                if experiment.project_id == project_id and experiment.name == name
            ),
            None,
        )

    def add_experiment(self, experiment: Experiment) -> None:
        self.experiments.append(experiment)

    def add_run(self, run: Run) -> None:
        self.runs[run.id] = run

    def run(self, run_id: PublicId) -> Run | None:
        return self.runs.get(run_id)

    def save_run(self, run: Run) -> None:
        self.runs[run.id] = run

    def stale_running_runs(self, heartbeat_before: datetime) -> tuple[Run, ...]:
        return tuple(
            run
            for run in self.runs.values()
            if run.state is RunState.RUNNING
            and run.heartbeat_at is not None
            and run.heartbeat_at < heartbeat_before
        )

    def experiments_for_project(
        self, project_id: PublicId, *, include_archived: bool
    ) -> tuple[Experiment, ...]:
        return tuple(
            item
            for item in self.experiments
            if item.project_id == project_id and (include_archived or item.archived_at is None)
        )

    def archive_experiment(self, experiment_id: PublicId, at: datetime) -> None:
        self.experiments = [
            Experiment(item.id, item.project_id, item.name, item.created_at, at)
            if item.id == experiment_id
            else item
            for item in self.experiments
        ]

    def append_audit(self, event: AuditEvent) -> None:
        self.audits.append(event)

    def commit(self) -> None:
        self.commits += 1


def fixture() -> tuple[PublicId, PublicId, PublicId, MemoryRunUnitOfWork]:
    actor = PublicId.generate(ResourceKind.PRINCIPAL)
    project = PublicId.generate(ResourceKind.PROJECT)
    repository = PublicId.generate(ResourceKind.REPOSITORY)
    uow = MemoryRunUnitOfWork({(project, actor): ProjectRole.CONTRIBUTOR}, {(repository, project)})
    return actor, project, repository, uow


def test_create_resolves_experiment_and_starts_run() -> None:
    actor, project, repository, uow = fixture()
    now = datetime(2026, 8, 17, tzinfo=UTC)

    run = RunService(uow).create(
        actor, CreateRun(project, repository, "baseline", ("python", "train.py"), now)
    )

    assert run.state is RunState.RUNNING
    assert run.heartbeat_at == now
    assert len(uow.experiments) == 1
    assert uow.commits == 1


def test_finalization_is_idempotent_only_for_identical_evidence() -> None:
    actor, project, repository, uow = fixture()
    now = datetime(2026, 8, 17, tzinfo=UTC)
    service = RunService(uow)
    run = service.create(
        actor, CreateRun(project, repository, "baseline", ("python", "train.py"), now)
    )
    command = FinalizeRun(
        run.id,
        0,
        RunState.SUCCEEDED,
        "a" * 40,
        {"dvc": {"revision": "exp-1"}},
        now,
        provenance_status=RunProvenanceStatus.COMPLETE,
        dvc_experiment_revision="b" * 40,
    )

    first = service.finalize(actor, command)
    replay = service.finalize(actor, command)

    assert replay == first
    assert first.provenance_status is RunProvenanceStatus.COMPLETE
    assert first.dvc_experiment_revision == "b" * 40
    with pytest.raises(ResourceConflict):
        service.finalize(
            actor,
            FinalizeRun(
                run.id,
                1,
                RunState.FAILED,
                "a" * 40,
                command.evidence,
                now,
            ),
        )


def test_recovery_marks_only_stale_running_runs_incomplete() -> None:
    actor, project, repository, uow = fixture()
    started = datetime(2026, 8, 17, 10, tzinfo=UTC)
    service = RunService(uow)
    run = service.create(
        actor, CreateRun(project, repository, "baseline", ("python", "train.py"), started)
    )

    recovered = service.recover_incomplete(started + timedelta(minutes=6), timedelta(minutes=5))

    assert recovered == 1
    assert uow.runs[run.id].state is RunState.INCOMPLETE
    assert uow.runs[run.id].ended_at == started + timedelta(minutes=6)


def test_maintainer_archives_experiment_and_future_runs_are_rejected() -> None:
    actor, project, repository, uow = fixture()
    now = datetime(2026, 8, 17, tzinfo=UTC)
    service = RunService(uow)
    service.create(actor, CreateRun(project, repository, "baseline", ("train",), now))
    uow.roles[(project, actor)] = ProjectRole.MAINTAINER
    experiment = uow.experiments[0]

    service.archive_experiment(
        actor,
        project,
        experiment.id,
        PublicId.generate(ResourceKind.REQUEST),
        now,
    )

    assert uow.experiments[0].archived_at == now
    assert uow.audits[-1].action == "experiment.archive"
    with pytest.raises(ResourceConflict):
        service.create(actor, CreateRun(project, repository, "baseline", ("train",), now))
