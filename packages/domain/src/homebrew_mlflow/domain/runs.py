from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import Any

from .identifiers import PublicId, ResourceKind


class RunState(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    FINALIZING = "finalizing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    INCOMPLETE = "incomplete"


_TRANSITIONS: dict[RunState, frozenset[RunState]] = {
    RunState.CREATED: frozenset({RunState.RUNNING, RunState.INCOMPLETE}),
    RunState.RUNNING: frozenset({RunState.FINALIZING, RunState.INCOMPLETE}),
    RunState.FINALIZING: frozenset(
        {RunState.SUCCEEDED, RunState.FAILED, RunState.INTERRUPTED, RunState.INCOMPLETE}
    ),
    RunState.SUCCEEDED: frozenset(),
    RunState.FAILED: frozenset(),
    RunState.INTERRUPTED: frozenset(),
    RunState.INCOMPLETE: frozenset(),
}


class InvalidRunTransition(ValueError):
    pass


def transition_run(current: RunState, target: RunState) -> RunState:
    if target not in _TRANSITIONS[current]:
        raise InvalidRunTransition(f"cannot transition Run from {current} to {target}")
    return target


@dataclass(frozen=True, slots=True)
class Experiment:
    id: PublicId
    project_id: PublicId
    name: str
    created_at: datetime
    archived_at: datetime | None = None

    @classmethod
    def create(cls, project_id: PublicId, name: str, created_at: datetime) -> Experiment:
        if project_id.kind is not ResourceKind.PROJECT:
            raise ValueError("Experiment must belong to a Research Project")
        normalized = name.strip()
        if not normalized:
            raise ValueError("Experiment name is required")
        return cls(PublicId.generate(ResourceKind.EXPERIMENT), project_id, normalized, created_at)


@dataclass(frozen=True, slots=True)
class Run:
    id: PublicId
    project_id: PublicId
    experiment_id: PublicId
    repository_id: PublicId
    creator_principal_id: PublicId
    state: RunState
    command: tuple[str, ...]
    created_at: datetime
    started_at: datetime | None = None
    heartbeat_at: datetime | None = None
    ended_at: datetime | None = None
    exit_code: int | None = None
    finalization_digest: str | None = None
    git_commit_sha: str | None = None
    retry_of_run_id: PublicId | None = None
    finalization_evidence: dict[str, Any] | None = None
    pipeline_version_id: PublicId | None = None
    environment_specification_id: PublicId | None = None

    @classmethod
    def create(
        cls,
        project_id: PublicId,
        experiment_id: PublicId,
        repository_id: PublicId,
        creator_principal_id: PublicId,
        command: tuple[str, ...],
        created_at: datetime,
        retry_of_run_id: PublicId | None = None,
        pipeline_version_id: PublicId | None = None,
        environment_specification_id: PublicId | None = None,
    ) -> Run:
        if not command:
            raise ValueError("Run command is required")
        expected = (
            (project_id, ResourceKind.PROJECT),
            (experiment_id, ResourceKind.EXPERIMENT),
            (repository_id, ResourceKind.REPOSITORY),
            (creator_principal_id, ResourceKind.PRINCIPAL),
        )
        if any(identifier.kind is not kind for identifier, kind in expected):
            raise ValueError("Run identifiers have invalid resource kinds")
        if (
            pipeline_version_id is not None
            and pipeline_version_id.kind is not ResourceKind.PIPELINE_VERSION
        ):
            raise ValueError("invalid Pipeline Version identifier")
        if (
            environment_specification_id is not None
            and environment_specification_id.kind
            is not ResourceKind.ENVIRONMENT_SPECIFICATION
        ):
            raise ValueError("invalid Environment Specification identifier")
        return cls(
            PublicId.generate(ResourceKind.RUN),
            project_id,
            experiment_id,
            repository_id,
            creator_principal_id,
            RunState.CREATED,
            command,
            created_at,
            retry_of_run_id=retry_of_run_id,
            pipeline_version_id=pipeline_version_id,
            environment_specification_id=environment_specification_id,
        )

    def start(self, now: datetime) -> Run:
        return replace(
            self,
            state=transition_run(self.state, RunState.RUNNING),
            started_at=now,
            heartbeat_at=now,
        )

    def heartbeat(self, now: datetime) -> Run:
        if self.state is not RunState.RUNNING:
            raise InvalidRunTransition("only a running Run accepts heartbeats")
        return replace(self, heartbeat_at=now)

    def begin_finalization(self) -> Run:
        return replace(self, state=transition_run(self.state, RunState.FINALIZING))

    def mark_incomplete(self, now: datetime) -> Run:
        return replace(
            self,
            state=transition_run(self.state, RunState.INCOMPLETE),
            ended_at=now,
        )

    def finish(
        self,
        target: RunState,
        now: datetime,
        *,
        exit_code: int,
        finalization_digest: str,
        git_commit_sha: str | None,
        evidence: dict[str, Any],
    ) -> Run:
        if target not in {RunState.SUCCEEDED, RunState.FAILED, RunState.INTERRUPTED}:
            raise InvalidRunTransition("Run finalization requires a completed terminal state")
        return replace(
            self,
            state=transition_run(self.state, target),
            ended_at=now,
            exit_code=exit_code,
            finalization_digest=finalization_digest,
            git_commit_sha=git_commit_sha,
            finalization_evidence=evidence,
        )
