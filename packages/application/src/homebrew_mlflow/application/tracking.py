from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from homebrew_mlflow.domain import (
    MachineScope,
    ProjectRole,
    PublicId,
    Run,
    RunMetric,
    RunParameter,
    RunState,
    RunTag,
    permits,
)

from .projects import AuthorizationDenied, ResourceConflict


class TrackingUnitOfWork(Protocol):
    def run(self, run_id: PublicId) -> Run | None: ...

    def project_role(self, project_id: PublicId, principal_id: PublicId) -> ProjectRole | None: ...

    def parameter(self, run_id: PublicId, key: str) -> RunParameter | None: ...

    def add_parameter(self, parameter: RunParameter) -> None: ...

    def add_metric(self, metric: RunMetric) -> None: ...

    def upsert_tag(self, tag: RunTag) -> None: ...

    def list_parameters(self, run_id: PublicId) -> tuple[RunParameter, ...]: ...

    def list_metrics(self, run_id: PublicId) -> tuple[RunMetric, ...]: ...

    def list_tags(self, run_id: PublicId) -> tuple[RunTag, ...]: ...

    def tracking_snapshots_for_project(
        self, project_id: PublicId
    ) -> tuple[TrackingSnapshot, ...]: ...

    def commit(self) -> None: ...


@dataclass(frozen=True, slots=True)
class ParameterValue:
    key: str
    value: str


@dataclass(frozen=True, slots=True)
class MetricValue:
    key: str
    value: float
    timestamp_ms: int
    step: int


@dataclass(frozen=True, slots=True)
class TagValue:
    key: str
    value: str


@dataclass(frozen=True, slots=True)
class LogBatch:
    run_id: PublicId
    project_id: PublicId
    parameters: tuple[ParameterValue, ...]
    metrics: tuple[MetricValue, ...]
    tags: tuple[TagValue, ...]
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class TrackingSnapshot:
    run: Run
    parameters: tuple[RunParameter, ...]
    metrics: tuple[RunMetric, ...]
    tags: tuple[RunTag, ...]


class TrackingService:
    def __init__(self, unit_of_work: TrackingUnitOfWork) -> None:
        self._uow = unit_of_work

    def log_batch(self, actor_id: PublicId, batch: LogBatch) -> None:
        run = self._authorized_run(actor_id, batch.run_id, batch.project_id)
        if run.state is not RunState.RUNNING:
            raise ResourceConflict("only a running Run accepts tracking metadata")
        if len(batch.parameters) + len(batch.metrics) + len(batch.tags) > 1000:
            raise ValueError("tracking batch exceeds 1000 records")
        seen_parameters: dict[str, str] = {}
        for parameter_value in batch.parameters:
            prior = seen_parameters.setdefault(parameter_value.key, parameter_value.value)
            if prior != parameter_value.value:
                raise ResourceConflict("parameter has multiple values in one batch")
            parameter = RunParameter(
                batch.run_id,
                parameter_value.key,
                parameter_value.value,
                batch.occurred_at,
            )
            existing = self._uow.parameter(batch.run_id, parameter.key)
            if existing is not None:
                if existing.value != parameter.value:
                    raise ResourceConflict("Run parameters are immutable")
                continue
            self._uow.add_parameter(parameter)
        for metric_value in batch.metrics:
            self._uow.add_metric(
                RunMetric(
                    batch.run_id,
                    metric_value.key,
                    metric_value.value,
                    metric_value.timestamp_ms,
                    metric_value.step,
                    batch.occurred_at,
                )
            )
        for tag_value in batch.tags:
            self._uow.upsert_tag(
                RunTag(batch.run_id, tag_value.key, tag_value.value, batch.occurred_at)
            )
        self._uow.commit()

    def snapshot(
        self, actor_id: PublicId, run_id: PublicId, project_id: PublicId
    ) -> TrackingSnapshot:
        run = self._authorized_run(actor_id, run_id, project_id)
        return TrackingSnapshot(
            run,
            self._uow.list_parameters(run_id),
            self._uow.list_metrics(run_id),
            self._uow.list_tags(run_id),
        )

    def snapshot_for_actor(self, actor_id: PublicId, run_id: PublicId) -> TrackingSnapshot:
        run = self._uow.run(run_id)
        if run is None:
            raise ValueError("Run does not exist")
        role = self._uow.project_role(run.project_id, actor_id)
        if role is None or not permits(role, MachineScope.READ):
            raise AuthorizationDenied("project membership is required")
        return TrackingSnapshot(
            run,
            self._uow.list_parameters(run_id),
            self._uow.list_metrics(run_id),
            self._uow.list_tags(run_id),
        )

    def project_snapshots(
        self, actor_id: PublicId, project_id: PublicId
    ) -> tuple[TrackingSnapshot, ...]:
        role = self._uow.project_role(project_id, actor_id)
        if role is None or not permits(role, MachineScope.READ):
            raise AuthorizationDenied("project membership is required")
        return self._uow.tracking_snapshots_for_project(project_id)

    def _authorized_run(self, actor_id: PublicId, run_id: PublicId, project_id: PublicId) -> Run:
        run = self._uow.run(run_id)
        if run is None:
            raise ValueError("Run does not exist")
        if run.project_id != project_id:
            raise AuthorizationDenied("tracking credential is bound to a different project")
        role = self._uow.project_role(run.project_id, actor_id)
        if role is None or not permits(role, MachineScope.TRACK):
            raise AuthorizationDenied("Contributor role is required to log Run metadata")
        return run
