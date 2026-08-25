from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from homebrew_mlflow.application import (
    AuthorizationDenied,
    MetricProgressService,
    ProgressDisplayMode,
    ResourceConflict,
)
from homebrew_mlflow.domain import PublicId, ResourceKind
from homebrew_mlflow.infrastructure.database import (
    AuditEventRow,
    Base,
    ExperimentRow,
    GitRepositoryRow,
    OrganizationRow,
    PrincipalRow,
    ProjectMembershipRow,
    ResearchProjectRow,
    RunMetricRow,
    RunRow,
    SqlAlchemyMetricProgressUnitOfWork,
)
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session


def public_id(kind: ResourceKind) -> PublicId:
    return PublicId.generate(kind)


def test_progress_aggregates_latest_finite_values_for_active_experiments() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    now = datetime(2026, 8, 25, 10, tzinfo=UTC)
    organization_id = public_id(ResourceKind.ORGANIZATION)
    project_id = public_id(ResourceKind.PROJECT)
    principal_id = public_id(ResourceKind.PRINCIPAL)
    viewer_id = public_id(ResourceKind.PRINCIPAL)
    repository_id = public_id(ResourceKind.REPOSITORY)
    experiment_a = public_id(ResourceKind.EXPERIMENT)
    experiment_b = public_id(ResourceKind.EXPERIMENT)
    archived_experiment = public_id(ResourceKind.EXPERIMENT)
    keys = {
        "organization": uuid4(),
        "project": uuid4(),
        "principal": uuid4(),
        "viewer": uuid4(),
        "repository": uuid4(),
        "experiment_a": uuid4(),
        "experiment_b": uuid4(),
        "archived_experiment": uuid4(),
    }

    with Session(engine) as session:
        session.add(
            OrganizationRow(
                id=keys["organization"],
                public_id=str(organization_id),
                name="Research",
                created_at=now,
                archived_at=None,
            )
        )
        session.add(
            PrincipalRow(
                id=keys["principal"],
                public_id=str(principal_id),
                kind="human",
                display_name="Maintainer",
                created_at=now,
                archived_at=None,
            )
        )
        session.add(
            PrincipalRow(
                id=keys["viewer"],
                public_id=str(viewer_id),
                kind="human",
                display_name="Viewer",
                created_at=now,
                archived_at=None,
            )
        )
        session.add(
            ResearchProjectRow(
                id=keys["project"],
                public_id=str(project_id),
                organization_id=keys["organization"],
                name="Search",
                slug="search",
                created_at=now,
                archived_at=None,
                state="active",
            )
        )
        session.add(
            ProjectMembershipRow(
                project_id=keys["project"],
                principal_id=keys["principal"],
                role="maintainer",
                created_at=now,
            )
        )
        session.add(
            ProjectMembershipRow(
                project_id=keys["project"],
                principal_id=keys["viewer"],
                role="viewer",
                created_at=now,
            )
        )
        session.add(
            GitRepositoryRow(
                id=keys["repository"],
                public_id=str(repository_id),
                project_id=keys["project"],
                name="Search",
                slug="search",
                default_branch="main",
                state="active",
                created_at=now,
                updated_at=now,
            )
        )
        for key, experiment_id, name, archived_at in (
            ("experiment_a", experiment_a, "Candidate A", None),
            ("experiment_b", experiment_b, "Candidate B", None),
            ("archived_experiment", archived_experiment, "Archived", now),
        ):
            session.add(
                ExperimentRow(
                    id=keys[key],
                    public_id=str(experiment_id),
                    project_id=keys["project"],
                    name=name,
                    created_at=now,
                    archived_at=archived_at,
                )
            )

        def add_run(
            experiment_key: str, run_at: datetime, state: str = "succeeded"
        ) -> tuple[UUID, PublicId]:
            row_key = uuid4()
            run_id = public_id(ResourceKind.RUN)
            session.add(
                RunRow(
                    id=row_key,
                    public_id=str(run_id),
                    project_id=keys["project"],
                    experiment_id=keys[experiment_key],
                    repository_id=keys["repository"],
                    creator_principal_id=keys["principal"],
                    retry_of_run_id=None,
                    pipeline_version_id=None,
                    environment_specification_id=None,
                    state=state,
                    command=["python", "train.py"],
                    created_at=run_at - timedelta(hours=1),
                    started_at=run_at - timedelta(hours=1),
                    heartbeat_at=run_at,
                    ended_at=run_at if state != "running" else None,
                    exit_code=0 if state != "running" else None,
                    finalization_digest=None,
                    git_commit_sha=None,
                    provenance_status="complete" if state != "running" else "pending",
                    dvc_experiment_revision=None,
                    finalization_evidence=None,
                    finalization_idempotency_key=None,
                )
            )
            return row_key, run_id

        old_run, _ = add_run("experiment_a", now + timedelta(days=1))
        latest_run, latest_run_id = add_run("experiment_a", now + timedelta(days=3))
        running_run, _ = add_run("experiment_b", now + timedelta(days=2), "running")
        archived_run, _ = add_run("archived_experiment", now + timedelta(days=4))
        session.flush()
        for run_key, key, value in (
            (old_run, "score", 1.0),
            (old_run, "score", 1.5),
            (latest_run, "score", 2.0),
            (latest_run, "other", 9.0),
            (running_run, "score", 3.0),
            (archived_run, "score", 99.0),
        ):
            session.add(
                RunMetricRow(
                    run_id=run_key,
                    key=key,
                    value=value,
                    timestamp_ms=1,
                    step=0,
                    logged_at=now,
                )
            )
        session.commit()

        unit_of_work = SqlAlchemyMetricProgressUnitOfWork(session)
        catalog = unit_of_work.metric_progress_catalog(project_id)
        assert [metric.key for metric in catalog] == ["other", "score"]
        assert catalog[1].experiment_count == 2

        points = unit_of_work.metric_progress_points(project_id, "score")
        assert [(point.experiment_name, point.value) for point in points] == [
            ("Candidate B", 3.0),
            ("Candidate A", 2.0),
        ]
        assert points[1].run_id == latest_run_id

        result = MetricProgressService(unit_of_work).set_default(
            principal_id,
            project_id,
            "score",
            ProgressDisplayMode.MINIMIZE,
            public_id(ResourceKind.REQUEST),
            now,
        )
        assert result.default_metric_key == "score"
        assert result.default_display_mode is ProgressDisplayMode.MINIMIZE
        assert unit_of_work.default_progress_metric(project_id) == "score"
        assert unit_of_work.default_progress_metric_mode(project_id) is ProgressDisplayMode.MINIMIZE
        audit = session.scalar(
            select(AuditEventRow).where(
                AuditEventRow.action == "project.metric_progress_default.update"
            )
        )
        assert audit is not None
        assert audit.safe_metadata == {
            "previous_metric": None,
            "current_metric": "score",
            "previous_mode": "default",
            "current_mode": "minimize",
        }
        with pytest.raises(AuthorizationDenied):
            MetricProgressService(unit_of_work).set_default(
                viewer_id,
                project_id,
                "score",
                ProgressDisplayMode.MAXIMIZE,
                public_id(ResourceKind.REQUEST),
                now,
            )
        with pytest.raises(ResourceConflict):
            MetricProgressService(unit_of_work).set_default(
                principal_id,
                project_id,
                "missing",
                ProgressDisplayMode.DEFAULT,
                public_id(ResourceKind.REQUEST),
                now,
            )
        cleared = MetricProgressService(unit_of_work).set_default(
            principal_id,
            project_id,
            None,
            ProgressDisplayMode.MAXIMIZE,
            public_id(ResourceKind.REQUEST),
            now,
        )
        assert cleared.default_metric_key is None
        assert cleared.default_display_mode is ProgressDisplayMode.DEFAULT
