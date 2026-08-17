from dataclasses import dataclass, field

from homebrew_mlflow.application import (
    HostedNamespace,
    HostedRepository,
    ProjectProvisioningCoordinator,
    RepositoryProvisioningJob,
    RepositorySeedFile,
)
from homebrew_mlflow.domain import PublicId, ResourceKind


@dataclass
class MemoryStore:
    job: RepositoryProvisioningJob | None
    completions: list[tuple[RepositoryProvisioningJob, HostedNamespace]] = field(
        default_factory=list
    )
    failures: list[str] = field(default_factory=list)

    def claim_next(self, _worker_id: str) -> RepositoryProvisioningJob | None:
        job, self.job = self.job, None
        return job

    def complete(
        self,
        job: RepositoryProvisioningJob,
        namespace: HostedNamespace,
        _repository_provider_id: str,
        _repository_default_branch: str,
        _web_url: str,
        _http_clone_url: str,
        _ssh_clone_url: str,
    ) -> None:
        self.completions.append((job, namespace))

    def fail(
        self,
        _job: RepositoryProvisioningJob,
        failure_code: str,
        _namespace_id: str | None,
        _repository_provider_id: str | None,
    ) -> None:
        self.failures.append(failure_code)


class NamespaceHost:
    def create_private(self, _name: str, _slug: str) -> HostedNamespace:
        return HostedNamespace("7", "research/models")


class Template:
    def render(self, context) -> tuple[RepositorySeedFile, ...]:  # type: ignore[no-untyped-def]
        return (RepositorySeedFile("README.md", str(context.repository_id)),)


class RepositoryHost:
    def create_with_seed(self, request, files) -> HostedRepository:  # type: ignore[no-untyped-def]
        assert request.namespace_id == 7
        assert files[0].path == "README.md"
        return HostedRepository("9", "main", "https://git/repo", "https://git/repo.git", "ssh")


def job() -> RepositoryProvisioningJob:
    return RepositoryProvisioningJob(
        PublicId.generate(ResourceKind.PROJECT),
        "Models",
        "models",
        PublicId.generate(ResourceKind.REPOSITORY),
        "Models",
        "models",
        "main",
        None,
    )


def test_coordinator_creates_namespace_and_seeded_repository() -> None:
    store = MemoryStore(job())
    coordinator = ProjectProvisioningCoordinator(
        store,
        NamespaceHost(),
        RepositoryHost(),
        Template(),
        platform_url="https://ml.example",
        dvc_remote_base_url="s3://dvc",
        s3_endpoint_url="https://objects.example",
    )

    assert coordinator.run_once("worker-1")
    assert store.completions[0][1].provider_id == "7"
    assert not store.failures
    assert not coordinator.run_once("worker-1")
