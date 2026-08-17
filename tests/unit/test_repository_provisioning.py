from __future__ import annotations

from dataclasses import dataclass

import pytest
from homebrew_mlflow.application import (
    HostedRepository,
    HostedRepositoryRequest,
    ProvisionRepository,
    RepositoryProvisioningService,
    RepositorySeedFile,
    RepositoryTemplateContext,
)
from homebrew_mlflow.domain import PublicId, ResourceKind


@dataclass
class StubTemplate:
    received: RepositoryTemplateContext | None = None

    def render(self, context: RepositoryTemplateContext) -> tuple[RepositorySeedFile, ...]:
        self.received = context
        return (RepositorySeedFile("README.md", f"# {context.repository_name}\n"),)


@dataclass
class StubHost:
    received_request: HostedRepositoryRequest | None = None
    received_files: tuple[RepositorySeedFile, ...] = ()

    def create_with_seed(
        self,
        request: HostedRepositoryRequest,
        files: tuple[RepositorySeedFile, ...],
    ) -> HostedRepository:
        self.received_request = request
        self.received_files = files
        return HostedRepository(
            "42", "main", "https://git/repo", "https://git/repo.git", "git@git:repo"
        )


def command() -> ProvisionRepository:
    return ProvisionRepository(
        project_id=PublicId.generate(ResourceKind.PROJECT),
        project_name="Vision",
        namespace_id=7,
        repository_name="Detector",
        repository_slug="detector",
        platform_url="https://ml.example/",
        dvc_remote_url="s3://research/pr_123/dvc",
        s3_endpoint_url="https://objects.example/",
    )


def test_provisioning_renders_and_seeds_new_repository() -> None:
    template = StubTemplate()
    host = StubHost()

    result = RepositoryProvisioningService(template, host).provision(command())

    assert result.id.kind is ResourceKind.REPOSITORY
    assert result.seeded_paths == ("README.md",)
    assert template.received is not None
    assert template.received.repository_id == result.id
    assert template.received.platform_url == "https://ml.example"
    assert host.received_request == HostedRepositoryRequest(7, "Detector", "detector", "main")
    assert host.received_files[0].content == "# Detector\n"


def test_provisioning_rejects_non_project_parent() -> None:
    template = StubTemplate()
    host = StubHost()
    invalid = command()
    invalid = ProvisionRepository(
        project_id=PublicId.generate(ResourceKind.ORGANIZATION),
        project_name=invalid.project_name,
        namespace_id=invalid.namespace_id,
        repository_name=invalid.repository_name,
        repository_slug=invalid.repository_slug,
        platform_url=invalid.platform_url,
        dvc_remote_url=invalid.dvc_remote_url,
        s3_endpoint_url=invalid.s3_endpoint_url,
    )

    with pytest.raises(ValueError, match="Research Project"):
        RepositoryProvisioningService(template, host).provision(invalid)
