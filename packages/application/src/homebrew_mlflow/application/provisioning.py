from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from homebrew_mlflow.domain import PublicId

from .repositories import (
    GitRepositoryHost,
    HostedRepositoryRequest,
    RepositoryTemplate,
    RepositoryTemplateContext,
)


@dataclass(frozen=True, slots=True)
class HostedNamespace:
    provider_id: str
    full_path: str


class ProjectNamespaceHost(Protocol):
    def create_private(self, name: str, slug: str) -> HostedNamespace: ...


@dataclass(frozen=True, slots=True)
class RepositoryProvisioningJob:
    project_id: PublicId
    project_name: str
    project_slug: str
    repository_id: PublicId
    repository_name: str
    repository_slug: str
    default_branch: str
    namespace_id: str | None


class ProvisioningStore(Protocol):
    def claim_next(self, worker_id: str) -> RepositoryProvisioningJob | None: ...

    def complete(
        self,
        job: RepositoryProvisioningJob,
        namespace: HostedNamespace,
        repository_provider_id: str,
        repository_default_branch: str,
        web_url: str,
        http_clone_url: str,
        ssh_clone_url: str,
    ) -> None: ...

    def fail(
        self,
        job: RepositoryProvisioningJob,
        failure_code: str,
        namespace_id: str | None,
        repository_provider_id: str | None,
    ) -> None: ...


class ProjectProvisioningCoordinator:
    def __init__(
        self,
        store: ProvisioningStore,
        namespace_host: ProjectNamespaceHost,
        repository_host: GitRepositoryHost,
        template: RepositoryTemplate,
        *,
        platform_url: str,
        dvc_remote_base_url: str,
        s3_endpoint_url: str,
    ) -> None:
        self._store = store
        self._namespace_host = namespace_host
        self._repository_host = repository_host
        self._template = template
        self._platform_url = platform_url.rstrip("/")
        self._dvc_remote_base_url = dvc_remote_base_url.rstrip("/")
        self._s3_endpoint_url = s3_endpoint_url.rstrip("/")

    def run_once(self, worker_id: str) -> bool:
        job = self._store.claim_next(worker_id)
        if job is None:
            return False
        namespace: HostedNamespace | None = None
        repository_provider_id: str | None = None
        try:
            namespace = (
                HostedNamespace(job.namespace_id, job.project_slug)
                if job.namespace_id is not None
                else self._namespace_host.create_private(job.project_name, job.project_slug)
            )
            files = self._template.render(
                RepositoryTemplateContext(
                    repository_id=job.repository_id,
                    project_id=job.project_id,
                    project_name=job.project_name,
                    repository_name=job.repository_name,
                    repository_slug=job.repository_slug,
                    platform_url=self._platform_url,
                    dvc_remote_url=f"{self._dvc_remote_base_url}/{job.project_id}",
                    s3_endpoint_url=self._s3_endpoint_url,
                )
            )
            hosted = self._repository_host.create_with_seed(
                HostedRepositoryRequest(
                    namespace_id=int(namespace.provider_id),
                    name=job.repository_name,
                    slug=job.repository_slug,
                    default_branch=job.default_branch,
                ),
                files,
            )
            repository_provider_id = hosted.provider_id
            self._store.complete(
                job,
                namespace,
                hosted.provider_id,
                hosted.default_branch,
                hosted.web_url,
                hosted.http_clone_url,
                hosted.ssh_clone_url,
            )
        except Exception as error:
            self._store.fail(
                job,
                type(error).__name__,
                namespace.provider_id if namespace else job.namespace_id,
                repository_provider_id,
            )
        return True
