from __future__ import annotations

import os
import socket
import time
from datetime import UTC, datetime, timedelta

from homebrew_mlflow.application import ArtifactArchiveCoordinator
from homebrew_mlflow.infrastructure import (
    S3ArtifactArchiveBuilder,
    SqlAlchemyArtifactArchiveStore,
    create_session,
)


def run() -> None:
    database_url = os.environ["HOMEBREW_MLFLOW_DATABASE_URL"]
    builder = S3ArtifactArchiveBuilder(
        endpoint_url=os.environ["HOMEBREW_MLFLOW_S3_ENDPOINT_URL"],
        access_key_id=os.environ["HOMEBREW_MLFLOW_S3_ACCESS_KEY_ID"],
        secret_access_key=os.environ["HOMEBREW_MLFLOW_S3_SECRET_ACCESS_KEY"],
        destination_bucket=os.getenv("HOMEBREW_MLFLOW_ATTACHMENT_BUCKET", "homebrew-mlflow"),
    )
    worker_id = f"archive-{socket.gethostname()}"
    retention = timedelta(
        hours=int(os.getenv("HOMEBREW_MLFLOW_ARTIFACT_ARCHIVE_RETENTION_HOURS", "24"))
    )
    while True:
        with create_session(database_url) as session:
            worked = ArtifactArchiveCoordinator(
                SqlAlchemyArtifactArchiveStore(session), builder, retention=retention
            ).run_once(worker_id, datetime.now(UTC))
        if not worked:
            time.sleep(2)


if __name__ == "__main__":
    run()
