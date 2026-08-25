from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from homebrew_mlflow.domain import AuditEvent, MachineScope, ProjectRole, PublicId, permits

from .projects import AuthorizationDenied, ResourceConflict


@dataclass(frozen=True, slots=True)
class MetricProgressMetric:
    key: str
    experiment_count: int
    latest_run_at: datetime


@dataclass(frozen=True, slots=True)
class MetricProgressPoint:
    experiment_id: PublicId
    experiment_name: str
    run_id: PublicId
    run_state: str
    value: float
    run_at: datetime
    metric_timestamp_ms: int
    metric_step: int


@dataclass(frozen=True, slots=True)
class MetricProgressCatalog:
    default_metric_key: str | None
    metrics: tuple[MetricProgressMetric, ...]


class MetricProgressUnitOfWork(Protocol):
    def project_role(self, project_id: PublicId, principal_id: PublicId) -> ProjectRole | None: ...

    def default_progress_metric(self, project_id: PublicId) -> str | None: ...

    def metric_progress_catalog(
        self, project_id: PublicId
    ) -> tuple[MetricProgressMetric, ...]: ...

    def metric_progress_points(
        self, project_id: PublicId, metric_key: str
    ) -> tuple[MetricProgressPoint, ...]: ...

    def set_default_progress_metric(self, project_id: PublicId, metric_key: str | None) -> None: ...

    def append_audit(self, event: AuditEvent) -> None: ...

    def commit(self) -> None: ...


class MetricProgressService:
    def __init__(self, unit_of_work: MetricProgressUnitOfWork) -> None:
        self._uow = unit_of_work

    def catalog(self, actor_id: PublicId, project_id: PublicId) -> MetricProgressCatalog:
        self._require_read(actor_id, project_id)
        return MetricProgressCatalog(
            self._uow.default_progress_metric(project_id),
            self._uow.metric_progress_catalog(project_id),
        )

    def points(
        self, actor_id: PublicId, project_id: PublicId, metric_key: str
    ) -> tuple[MetricProgressPoint, ...]:
        self._require_read(actor_id, project_id)
        normalized = metric_key.strip()
        if not normalized or len(normalized) > 250:
            raise ValueError("metric key must contain between 1 and 250 characters")
        if normalized not in {value.key for value in self._uow.metric_progress_catalog(project_id)}:
            raise ResourceConflict("metric is not available in an active Experiment")
        return self._uow.metric_progress_points(project_id, normalized)

    def set_default(
        self,
        actor_id: PublicId,
        project_id: PublicId,
        metric_key: str | None,
        request_id: PublicId,
        now: datetime,
    ) -> MetricProgressCatalog:
        if self._uow.project_role(project_id, actor_id) is not ProjectRole.MAINTAINER:
            raise AuthorizationDenied("project Maintainer role is required")
        normalized = metric_key.strip() if metric_key is not None else None
        if normalized == "":
            normalized = None
        metrics = self._uow.metric_progress_catalog(project_id)
        if normalized is not None and normalized not in {value.key for value in metrics}:
            raise ResourceConflict("default metric is not available in an active Experiment")
        previous = self._uow.default_progress_metric(project_id)
        self._uow.set_default_progress_metric(project_id, normalized)
        self._uow.append_audit(
            AuditEvent(
                actor_principal_id=actor_id,
                action="project.metric_progress_default.update",
                resource_type="research_project",
                resource_id=project_id,
                outcome="success",
                request_id=request_id,
                project_id=project_id,
                safe_metadata={"previous": previous, "current": normalized},
                occurred_at=now,
            )
        )
        self._uow.commit()
        return MetricProgressCatalog(normalized, metrics)

    def _require_read(self, actor_id: PublicId, project_id: PublicId) -> None:
        role = self._uow.project_role(project_id, actor_id)
        if role is None or not permits(role, MachineScope.READ):
            raise AuthorizationDenied("project membership is required")
