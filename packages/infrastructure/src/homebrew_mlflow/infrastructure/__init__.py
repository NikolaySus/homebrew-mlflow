"""Adapters for persistence and external services."""

from .attachment_objects import S3AttachmentObjectStore
from .database import (
    Base,
    SqlAlchemyArtifactCatalogUnitOfWork,
    SqlAlchemyAttachmentUnitOfWork,
    SqlAlchemyAuditUnitOfWork,
    SqlAlchemyEnvironmentUnitOfWork,
    SqlAlchemyGitLabIdentityStore,
    SqlAlchemyIdentityReadStore,
    SqlAlchemyMachineCredentialStore,
    SqlAlchemyMembershipUnitOfWork,
    SqlAlchemyOrganizationMembershipUnitOfWork,
    SqlAlchemyPipelineUnitOfWork,
    SqlAlchemyProjectUnitOfWork,
    SqlAlchemyProvisioningStore,
    SqlAlchemyPublicationUnitOfWork,
    SqlAlchemyPublicationWorkStore,
    SqlAlchemyRefreshCredentialStore,
    SqlAlchemyRepositoryUnitOfWork,
    SqlAlchemyRunUnitOfWork,
    SqlAlchemySecretContextUnitOfWork,
    SqlAlchemySetupStore,
    SqlAlchemySharingUnitOfWork,
    SqlAlchemyTrackingUnitOfWork,
    create_session,
    database_is_ready,
)
from .dvc_credentials import MinioDvcCredentialIssuer
from .gitlab_namespaces import GitLabNamespaceHost
from .gitlab_oauth import (
    DeviceAuthorization,
    DevicePollResult,
    DevicePollStatus,
    GitLabDeviceOAuthClient,
    GitLabIdentity,
    GitLabOAuthProtocolError,
)
from .gitlab_reconciliation import GitLabMembershipReconciler
from .gitlab_repositories import GitLabRepositoryHost, GitLabRepositoryProvisioningError
from .infisical_reconciliation import InfisicalMembershipReconciler
from .pipeline_sources import GitLabPipelineSourceReader
from .publication_validator import GitLabDvcPublicationValidator
from .repository_template import (
    FileSystemRepositoryTemplate,
    RepositoryTemplateError,
)
from .retention import S3RetentionCoordinator

__all__ = [
    "Base",
    "SqlAlchemyAuditUnitOfWork",
    "SqlAlchemyArtifactCatalogUnitOfWork",
    "S3AttachmentObjectStore",
    "SqlAlchemyAttachmentUnitOfWork",
    "SqlAlchemyProjectUnitOfWork",
    "SqlAlchemyProvisioningStore",
    "SqlAlchemyGitLabIdentityStore",
    "SqlAlchemyEnvironmentUnitOfWork",
    "SqlAlchemyIdentityReadStore",
    "SqlAlchemyMachineCredentialStore",
    "SqlAlchemyMembershipUnitOfWork",
    "SqlAlchemyOrganizationMembershipUnitOfWork",
    "SqlAlchemyPipelineUnitOfWork",
    "SqlAlchemyRepositoryUnitOfWork",
    "SqlAlchemyRunUnitOfWork",
    "SqlAlchemySecretContextUnitOfWork",
    "SqlAlchemySharingUnitOfWork",
    "SqlAlchemyTrackingUnitOfWork",
    "SqlAlchemyPublicationUnitOfWork",
    "SqlAlchemyPublicationWorkStore",
    "SqlAlchemyRefreshCredentialStore",
    "SqlAlchemySetupStore",
    "create_session",
    "database_is_ready",
    "DeviceAuthorization",
    "MinioDvcCredentialIssuer",
    "DevicePollResult",
    "DevicePollStatus",
    "GitLabDeviceOAuthClient",
    "GitLabIdentity",
    "GitLabMembershipReconciler",
    "GitLabOAuthProtocolError",
    "GitLabRepositoryHost",
    "GitLabRepositoryProvisioningError",
    "InfisicalMembershipReconciler",
    "GitLabPipelineSourceReader",
    "FileSystemRepositoryTemplate",
    "RepositoryTemplateError",
    "S3RetentionCoordinator",
    "GitLabDvcPublicationValidator",
    "GitLabNamespaceHost",
]
