from datetime import UTC, datetime

import pytest
from homebrew_mlflow.domain import (
    GitRepository,
    InvalidRepositoryTransition,
    PublicId,
    RepositoryState,
    ResourceKind,
)


def repository() -> GitRepository:
    return GitRepository.provisioning(
        PublicId.generate(ResourceKind.PROJECT),
        "Models",
        "models",
        "main",
        datetime.now(UTC),
    )


def test_repository_activates_only_with_complete_provider_coordinates() -> None:
    pending = repository()
    active = pending.activate(
        provider_id="42",
        web_url="https://git.example/research/models",
        http_clone_url="https://git.example/research/models.git",
        ssh_clone_url="git@git.example:research/models.git",
        default_branch="main",
    )

    assert active.state is RepositoryState.ACTIVE
    assert active.failure_code is None
    with pytest.raises(InvalidRepositoryTransition):
        active.fail("provider_failed")


def test_failed_repository_retains_provider_id_for_retry_and_drift_visibility() -> None:
    failed = repository().fail("template_commit_failed", provider_id="42")

    assert failed.state is RepositoryState.FAILED
    assert failed.provider_id == "42"
    assert failed.failure_code == "template_commit_failed"

    retried = failed.retry_provisioning()
    assert retried.state is RepositoryState.PROVISIONING
    assert retried.provider_id == "42"
    assert retried.failure_code is None
