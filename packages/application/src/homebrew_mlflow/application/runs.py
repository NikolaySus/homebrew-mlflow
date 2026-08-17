from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

from homebrew_mlflow.domain import (
    AuditEvent,
    Experiment,
    MachineScope,
    ProjectRole,
    PublicId,
    ResourceKind,
    Run,
    RunState,
    permits,
)

from .projects import AuthorizationDenied, ResourceConflict


class RunUnitOfWork(Protocol):
    def project_role(self, project_id: PublicId, principal_id: PublicId) -> ProjectRole | None: ...

    def repository_belongs_to_project(
        self, repository_id: PublicId, project_id: PublicId
    ) -> bool: ...

    def pipeline_version_belongs_to_project(
        self, pipeline_version_id: PublicId, project_id: PublicId
    ) -> bool: ...

    def environment_belongs_to_project(
        self, environment_id: PublicId, project_id: PublicId
    ) -> bool: ...

    def experiment_by_name(self, project_id: PublicId, name: str) -> Experiment | None: ...

    def add_experiment(self, experiment: Experiment) -> None: ...

    def add_run(self, run: Run) -> None: ...

    def run(self, run_id: PublicId) -> Run | None: ...

    def save_run(self, run: Run) -> None: ...

    def stale_running_runs(self, heartbeat_before: datetime) -> tuple[Run, ...]: ...

    def runs_for_project(self, project_id: PublicId) -> tuple[Run, ...]: ...

    def experiments_for_project(
        self, project_id: PublicId, *, include_archived: bool
    ) -> tuple[Experiment, ...]: ...

    def archive_experiment(self, experiment_id: PublicId, at: datetime) -> None: ...

    def append_audit(self, event: AuditEvent) -> None: ...

    def run_inputs(self, run_id: PublicId) -> tuple[PublicId, ...]: ...

    def run_outputs(self, run_id: PublicId) -> tuple[PublicId, ...]: ...

    def artifact_version_available_to_project(
        self, version_id: PublicId, project_id: PublicId, at: datetime
    ) -> bool: ...

    def add_run_input(
        self, run_id: PublicId, version_id: PublicId, occurred_at: datetime
    ) -> None: ...

    def commit(self) -> None: ...


@dataclass(frozen=True, slots=True)
class CreateRun:
    project_id: PublicId
    repository_id: PublicId
    experiment_name: str
    command: tuple[str, ...]
    occurred_at: datetime
    retry_of_run_id: PublicId | None = None
    pipeline_version_id: PublicId | None = None
    environment_specification_id: PublicId | None = None


@dataclass(frozen=True, slots=True)
class FinalizeRun:
    run_id: PublicId
    exit_code: int
    status: RunState
    git_commit_sha: str | None
    evidence: dict[str, Any]
    occurred_at: datetime
    pipeline_version_id: PublicId | None = None
    environment_specification_id: PublicId | None = None


@dataclass(frozen=True, slots=True)
class RunProvenance:
    run: Run
    input_artifact_version_ids: tuple[PublicId, ...]
    output_artifact_version_ids: tuple[PublicId, ...]


class RunService:
    def __init__(self, unit_of_work: RunUnitOfWork) -> None:
        self._uow = unit_of_work

    def create(self, actor_id: PublicId, command: CreateRun) -> Run:
        role = self._uow.project_role(command.project_id, actor_id)
        if role is None or not permits(role, MachineScope.TRACK):
            raise AuthorizationDenied("Contributor role is required to create a Run")
        if not self._uow.repository_belongs_to_project(command.repository_id, command.project_id):
            raise ValueError("repository is not active in the selected project")
        valid_pipeline = command.pipeline_version_id is None or (
            self._uow.pipeline_version_belongs_to_project(
                command.pipeline_version_id, command.project_id
            )
        )
        if not valid_pipeline:
            raise ValueError("Pipeline Version is not active in the selected project")
        valid_environment = command.environment_specification_id is None or (
            self._uow.environment_belongs_to_project(
                command.environment_specification_id, command.project_id
            )
        )
        if not valid_environment:
            raise ValueError("Environment Specification is not active in the selected project")
        experiment = self._uow.experiment_by_name(
            command.project_id, command.experiment_name.strip()
        )
        if experiment is None:
            experiment = Experiment.create(
                command.project_id, command.experiment_name, command.occurred_at
            )
            self._uow.add_experiment(experiment)
        elif experiment.archived_at is not None:
            raise ResourceConflict("Experiment is archived")
        run = Run.create(
            command.project_id,
            experiment.id,
            command.repository_id,
            actor_id,
            command.command,
            command.occurred_at,
            command.retry_of_run_id,
            command.pipeline_version_id,
            command.environment_specification_id,
        ).start(command.occurred_at)
        self._uow.add_run(run)
        self._uow.commit()
        return run

    def heartbeat(self, actor_id: PublicId, run_id: PublicId, occurred_at: datetime) -> Run:
        run = self._required_run(run_id)
        self._authorize_run_actor(actor_id, run)
        updated = run.heartbeat(occurred_at)
        self._uow.save_run(updated)
        self._uow.commit()
        return updated

    def finalize(self, actor_id: PublicId, command: FinalizeRun) -> Run:
        run = self._required_run(command.run_id)
        self._authorize_run_actor(actor_id, run)
        valid_pipeline = command.pipeline_version_id is None or (
            self._uow.pipeline_version_belongs_to_project(
                command.pipeline_version_id, run.project_id
            )
        )
        if not valid_pipeline:
            raise ValueError("Pipeline Version is not active in the selected project")
        valid_environment = command.environment_specification_id is None or (
            self._uow.environment_belongs_to_project(
                command.environment_specification_id, run.project_id
            )
        )
        if not valid_environment:
            raise ValueError("Environment Specification is not active in the selected project")
        digest = hashlib.sha256(
            json.dumps(
                {
                    "exit_code": command.exit_code,
                    "status": command.status.value,
                    "git_commit_sha": command.git_commit_sha,
                    "evidence": command.evidence,
                    "pipeline_version_id": str(command.pipeline_version_id)
                    if command.pipeline_version_id
                    else None,
                    "environment_specification_id": str(
                        command.environment_specification_id
                    )
                    if command.environment_specification_id
                    else None,
                },
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        if run.state in {
            RunState.SUCCEEDED,
            RunState.FAILED,
            RunState.INTERRUPTED,
        }:
            if run.finalization_digest != digest:
                raise ResourceConflict("Run was finalized with different evidence")
            return run
        updated = run.begin_finalization().finish(
            command.status,
            command.occurred_at,
            exit_code=command.exit_code,
            finalization_digest=digest,
            git_commit_sha=command.git_commit_sha,
            evidence=command.evidence,
            pipeline_version_id=command.pipeline_version_id,
            environment_specification_id=command.environment_specification_id,
        )
        input_values = command.evidence.get("input_artifact_version_ids", [])
        if not isinstance(input_values, list) or not all(
            isinstance(value, str) for value in input_values
        ):
            raise ValueError("input Artifact Version evidence is invalid")
        for value in input_values:
            try:
                version_id = PublicId(ResourceKind.ARTIFACT_VERSION, value)
            except ValueError as error:
                raise ValueError("input Artifact Version evidence is invalid") from error
            if not self._uow.artifact_version_available_to_project(
                version_id, run.project_id, command.occurred_at
            ):
                raise AuthorizationDenied("input Artifact Version is not available to the Run")
            self._uow.add_run_input(run.id, version_id, command.occurred_at)
        self._uow.save_run(updated)
        self._uow.commit()
        return updated

    def recover_incomplete(self, now: datetime, heartbeat_timeout: timedelta) -> int:
        if heartbeat_timeout <= timedelta(0):
            raise ValueError("heartbeat timeout must be positive")
        stale = self._uow.stale_running_runs(now - heartbeat_timeout)
        for run in stale:
            self._uow.save_run(run.mark_incomplete(now))
        if stale:
            self._uow.commit()
        return len(stale)

    def list_project(self, actor_id: PublicId, project_id: PublicId) -> tuple[Run, ...]:
        role = self._uow.project_role(project_id, actor_id)
        if role is None or not permits(role, MachineScope.READ):
            raise AuthorizationDenied("project membership is required")
        return self._uow.runs_for_project(project_id)

    def list_experiments(
        self, actor_id: PublicId, project_id: PublicId, *, include_archived: bool = False
    ) -> tuple[Experiment, ...]:
        role = self._uow.project_role(project_id, actor_id)
        if role is None or not permits(role, MachineScope.READ):
            raise AuthorizationDenied("project membership is required")
        return self._uow.experiments_for_project(
            project_id, include_archived=include_archived
        )

    def archive_experiment(
        self,
        actor_id: PublicId,
        project_id: PublicId,
        experiment_id: PublicId,
        request_id: PublicId,
        now: datetime,
    ) -> None:
        if self._uow.project_role(project_id, actor_id) is not ProjectRole.MAINTAINER:
            raise AuthorizationDenied("project Maintainer role is required")
        experiments = self._uow.experiments_for_project(project_id, include_archived=True)
        experiment = next((item for item in experiments if item.id == experiment_id), None)
        if experiment is None:
            raise ValueError("Experiment does not exist in the selected project")
        if experiment.archived_at is None:
            self._uow.archive_experiment(experiment_id, now)
            self._uow.append_audit(
                AuditEvent(
                    actor_principal_id=actor_id,
                    action="experiment.archive",
                    resource_type="experiment",
                    resource_id=experiment_id,
                    outcome="success",
                    request_id=request_id,
                    project_id=project_id,
                    safe_metadata={},
                    occurred_at=now,
                )
            )
            self._uow.commit()

    def provenance(self, actor_id: PublicId, run_id: PublicId) -> RunProvenance:
        run = self._required_run(run_id)
        role = self._uow.project_role(run.project_id, actor_id)
        if role is None or not permits(role, MachineScope.READ):
            raise AuthorizationDenied("project membership is required")
        return RunProvenance(run, self._uow.run_inputs(run.id), self._uow.run_outputs(run.id))

    def _required_run(self, run_id: PublicId) -> Run:
        run = self._uow.run(run_id)
        if run is None:
            raise ValueError("Run does not exist")
        return run

    def _authorize_run_actor(self, actor_id: PublicId, run: Run) -> None:
        role = self._uow.project_role(run.project_id, actor_id)
        if actor_id != run.creator_principal_id and role is not ProjectRole.MAINTAINER:
            raise AuthorizationDenied("Run creator or project Maintainer is required")
