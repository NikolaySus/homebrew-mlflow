from datetime import UTC, datetime

import pytest
from homebrew_mlflow.domain import (
    InvalidRunTransition,
    PublicId,
    ResourceKind,
    Run,
    RunState,
    transition_run,
)


def test_run_happy_path() -> None:
    state = transition_run(RunState.CREATED, RunState.RUNNING)
    state = transition_run(state, RunState.FINALIZING)
    assert transition_run(state, RunState.SUCCEEDED) is RunState.SUCCEEDED


def test_terminal_run_cannot_transition() -> None:
    with pytest.raises(InvalidRunTransition):
        transition_run(RunState.SUCCEEDED, RunState.RUNNING)


def test_run_tracks_heartbeat_and_immutable_terminal_evidence() -> None:
    now = datetime(2026, 8, 17, tzinfo=UTC)
    run = Run.create(
        PublicId.generate(ResourceKind.PROJECT),
        PublicId.generate(ResourceKind.EXPERIMENT),
        PublicId.generate(ResourceKind.REPOSITORY),
        PublicId.generate(ResourceKind.PRINCIPAL),
        ("python", "train.py"),
        now,
    ).start(now)
    finished = (
        run.heartbeat(now)
        .begin_finalization()
        .finish(
            RunState.SUCCEEDED,
            now,
            exit_code=0,
            finalization_digest="a" * 64,
            git_commit_sha="b" * 40,
            evidence={},
        )
    )

    assert finished.state is RunState.SUCCEEDED
    assert finished.exit_code == 0
    assert finished.finalization_digest == "a" * 64
    with pytest.raises(InvalidRunTransition):
        finished.heartbeat(now)


def test_incomplete_run_can_only_reconcile_toward_finalization() -> None:
    now = datetime(2026, 8, 17, tzinfo=UTC)
    incomplete = Run.create(
        PublicId.generate(ResourceKind.PROJECT),
        PublicId.generate(ResourceKind.EXPERIMENT),
        PublicId.generate(ResourceKind.REPOSITORY),
        PublicId.generate(ResourceKind.PRINCIPAL),
        ("train",),
        now,
    ).start(now).mark_incomplete(now)

    assert incomplete.begin_reconciliation().state is RunState.FINALIZING
    with pytest.raises(InvalidRunTransition):
        incomplete.begin_finalization()
