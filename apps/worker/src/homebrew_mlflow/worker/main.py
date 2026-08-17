from __future__ import annotations

import os
import socket
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from homebrew_mlflow.application import (
    ProjectProvisioningCoordinator,
    PublicationCoordinator,
    RunService,
)
from homebrew_mlflow.infrastructure import (
    FileSystemRepositoryTemplate,
    GitLabDvcPublicationValidator,
    GitLabMembershipReconciler,
    GitLabNamespaceHost,
    GitLabRepositoryHost,
    InfisicalMembershipReconciler,
    InfisicalProjectProvisioner,
    S3RetentionCoordinator,
    SqlAlchemyProvisioningStore,
    SqlAlchemyPublicationUnitOfWork,
    SqlAlchemyPublicationWorkStore,
    SqlAlchemyRunUnitOfWork,
    create_session,
)


def _secret(name: str, file_name: str) -> str:
    path = os.environ.get(file_name)
    if path:
        value = Path(path).read_text(encoding="utf-8").strip()
        if not value:
            raise RuntimeError(f"secret file configured by {file_name} is empty")
        return value
    return os.environ[name]


def run() -> None:
    database_url = os.environ["HOMEBREW_MLFLOW_DATABASE_URL"]
    gitlab_url = os.environ["HOMEBREW_MLFLOW_GITLAB_BASE_URL"]
    gitlab_token = _secret(
        "HOMEBREW_MLFLOW_GITLAB_INTEGRATION_TOKEN",
        "HOMEBREW_MLFLOW_GITLAB_INTEGRATION_TOKEN_FILE",
    )
    platform_url = os.environ["HOMEBREW_MLFLOW_PUBLIC_BASE_URL"]
    dvc_remote = os.environ["HOMEBREW_MLFLOW_DVC_REMOTE_BASE_URL"]
    s3_endpoint = os.environ["HOMEBREW_MLFLOW_S3_ENDPOINT_URL"]
    s3_bucket = os.environ.get("HOMEBREW_MLFLOW_DVC_BUCKET", "research")
    attachment_bucket = os.environ.get("HOMEBREW_MLFLOW_ATTACHMENT_BUCKET", "homebrew-mlflow")
    s3_access_key = os.environ["HOMEBREW_MLFLOW_S3_ACCESS_KEY_ID"]
    s3_secret_key = os.environ["HOMEBREW_MLFLOW_S3_SECRET_ACCESS_KEY"]
    infisical_url = os.environ["HOMEBREW_MLFLOW_INFISICAL_BASE_URL"]
    infisical_token = _secret(
        "HOMEBREW_MLFLOW_INFISICAL_RECONCILIATION_TOKEN",
        "HOMEBREW_MLFLOW_INFISICAL_RECONCILIATION_TOKEN_FILE",
    )
    template = FileSystemRepositoryTemplate(
        Path(os.getenv("HOMEBREW_MLFLOW_REPOSITORY_TEMPLATE", "/app/repository_template"))
    )
    worker_id = f"integration-{socket.gethostname()}"
    heartbeat_timeout = timedelta(
        seconds=int(os.getenv("HOMEBREW_MLFLOW_RUN_HEARTBEAT_TIMEOUT_SECONDS", "300"))
    )
    event_retention = timedelta(days=int(os.getenv("HOMEBREW_MLFLOW_SSE_RETENTION_DAYS", "7")))
    next_event_retention = 0.0
    next_object_retention = 0.0

    while True:
        with create_session(database_url) as session:
            if time.monotonic() >= next_event_retention:
                SqlAlchemyPublicationUnitOfWork(session).prune_events(
                    datetime.now(UTC) - event_retention
                )
                next_event_retention = time.monotonic() + 3600
            if (
                os.getenv("HOMEBREW_MLFLOW_ENABLE_OBJECT_RETENTION", "false").lower() == "true"
                and time.monotonic() >= next_object_retention
            ):
                S3RetentionCoordinator(
                    session,
                    endpoint_url=s3_endpoint,
                    access_key_id=s3_access_key,
                    secret_access_key=s3_secret_key,
                    dvc_bucket=s3_bucket,
                    attachment_bucket=attachment_bucket,
                    provisional_retention=timedelta(
                        days=int(os.getenv("HOMEBREW_MLFLOW_PROVISIONAL_RETENTION_DAYS", "30"))
                    ),
                    attachment_retention=timedelta(
                        days=int(os.getenv("HOMEBREW_MLFLOW_ATTACHMENT_RETENTION_DAYS", "180"))
                    ),
                ).run(datetime.now(UTC))
                next_object_retention = time.monotonic() + 24 * 3600
            recovered = RunService(SqlAlchemyRunUnitOfWork(session)).recover_incomplete(
                datetime.now(UTC), heartbeat_timeout
            )
            secrets_provisioned = InfisicalProjectProvisioner(
                session, base_url=infisical_url, access_token=infisical_token
            ).run_once(datetime.now(UTC))
            reconciled = InfisicalMembershipReconciler(
                session, base_url=infisical_url, access_token=infisical_token
            ).run_once(datetime.now(UTC))
            gitlab_reconciled = GitLabMembershipReconciler(
                session, base_url=gitlab_url, access_token=gitlab_token
            ).run_once(datetime.now(UTC))
            published = PublicationCoordinator(
                SqlAlchemyPublicationWorkStore(session),
                GitLabDvcPublicationValidator(
                    session,
                    gitlab_url=gitlab_url,
                    gitlab_token=gitlab_token,
                    s3_endpoint_url=s3_endpoint,
                    s3_bucket=s3_bucket,
                    s3_access_key_id=s3_access_key,
                    s3_secret_access_key=s3_secret_key,
                    max_bytes=int(
                        os.getenv("HOMEBREW_MLFLOW_PUBLICATION_MAX_BYTES", str(100 * 1024**3))
                    ),
                    max_objects=int(
                        os.getenv("HOMEBREW_MLFLOW_PUBLICATION_MAX_OBJECTS", "1000000")
                    ),
                    max_seconds=int(os.getenv("HOMEBREW_MLFLOW_PUBLICATION_MAX_SECONDS", "1800")),
                ),
            ).run_once(worker_id, datetime.now(UTC))
            coordinator = ProjectProvisioningCoordinator(
                SqlAlchemyProvisioningStore(session),
                GitLabNamespaceHost(gitlab_url, gitlab_token),
                GitLabRepositoryHost(gitlab_url, gitlab_token),
                template,
                platform_url=platform_url,
                dvc_remote_base_url=dvc_remote,
                s3_endpoint_url=s3_endpoint,
            )
            worked = coordinator.run_once(worker_id)
        if (
            not worked
            and not secrets_provisioned
            and not published
            and not reconciled
            and not gitlab_reconciled
            and recovered == 0
        ):
            time.sleep(2)


if __name__ == "__main__":
    run()
