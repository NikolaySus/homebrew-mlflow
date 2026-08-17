from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest
from homebrew_mlflow.application import (
    LogBatch,
    MetricValue,
    ParameterValue,
    ResourceConflict,
    TagValue,
    TrackingService,
)
from homebrew_mlflow.domain import (
    ProjectRole,
    PublicId,
    ResourceKind,
    Run,
    RunMetric,
    RunParameter,
    RunTag,
)

NOW = datetime(2026, 8, 17, 12, tzinfo=UTC)


@dataclass
class UnitOfWork:
    stored_run: Run
    actor_id: PublicId
    parameters: dict[str, RunParameter] = field(default_factory=dict)
    metrics: list[RunMetric] = field(default_factory=list)
    tags: dict[str, RunTag] = field(default_factory=dict)
    commits: int = 0

    def run(self, run_id: PublicId) -> Run | None:
        return self.stored_run if self.stored_run.id == run_id else None

    def project_role(self, project_id: PublicId, principal_id: PublicId) -> ProjectRole | None:
        if project_id == self.stored_run.project_id and principal_id == self.actor_id:
            return ProjectRole.CONTRIBUTOR
        return None

    def parameter(self, _run_id: PublicId, key: str) -> RunParameter | None:
        return self.parameters.get(key)

    def add_parameter(self, parameter: RunParameter) -> None:
        self.parameters[parameter.key] = parameter

    def add_metric(self, metric: RunMetric) -> None:
        self.metrics.append(metric)

    def upsert_tag(self, tag: RunTag) -> None:
        self.tags[tag.key] = tag

    def list_parameters(self, _run_id: PublicId) -> tuple[RunParameter, ...]:
        return tuple(self.parameters.values())

    def list_metrics(self, _run_id: PublicId) -> tuple[RunMetric, ...]:
        return tuple(self.metrics)

    def list_tags(self, _run_id: PublicId) -> tuple[RunTag, ...]:
        return tuple(self.tags.values())

    def commit(self) -> None:
        self.commits += 1


def running_run(actor_id: PublicId) -> Run:
    return Run.create(
        PublicId.generate(ResourceKind.PROJECT),
        PublicId.generate(ResourceKind.EXPERIMENT),
        PublicId.generate(ResourceKind.REPOSITORY),
        actor_id,
        ("python", "train.py"),
        NOW,
    ).start(NOW)


def test_tracking_batch_persists_immutable_params_metric_history_and_mutable_tags() -> None:
    actor_id = PublicId.generate(ResourceKind.PRINCIPAL)
    run = running_run(actor_id)
    uow = UnitOfWork(run, actor_id)
    service = TrackingService(uow)
    batch = LogBatch(
        run.id,
        run.project_id,
        (ParameterValue("learning_rate", "0.01"),),
        (MetricValue("loss", 0.5, 1000, 1), MetricValue("loss", 0.25, 2000, 2)),
        (TagValue("model", "resnet"),),
        NOW,
    )

    service.log_batch(actor_id, batch)
    service.log_batch(actor_id, batch)

    assert uow.parameters["learning_rate"].value == "0.01"
    assert [metric.value for metric in uow.metrics] == [0.5, 0.25, 0.5, 0.25]
    assert uow.tags["model"].value == "resnet"
    assert uow.commits == 2


def test_tracking_rejects_parameter_mutation_and_reserved_tags() -> None:
    actor_id = PublicId.generate(ResourceKind.PRINCIPAL)
    run = running_run(actor_id)
    uow = UnitOfWork(run, actor_id)
    uow.parameters["seed"] = RunParameter(run.id, "seed", "1", NOW)
    service = TrackingService(uow)

    with pytest.raises(ResourceConflict, match="immutable"):
        service.log_batch(
            actor_id,
            LogBatch(
                run.id,
                run.project_id,
                (ParameterValue("seed", "2"),),
                (),
                (),
                NOW,
            ),
        )
    with pytest.raises(ValueError, match="reserved"):
        service.log_batch(
            actor_id,
            LogBatch(
                run.id,
                run.project_id,
                (),
                (),
                (TagValue("homebrew.secret", "value"),),
                NOW,
            ),
        )
