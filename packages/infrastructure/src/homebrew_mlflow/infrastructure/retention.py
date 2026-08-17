from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import boto3  # type: ignore[import-untyped]
from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import (
    ArtifactStorageLocationRow,
    ArtifactVersionFileRow,
    ArtifactVersionRow,
    ResearchProjectRow,
    RunAttachmentRow,
)


class S3RetentionCoordinator:
    """Apply archive-first deployment retention within the two owned buckets only."""

    def __init__(
        self,
        session: Session,
        *,
        endpoint_url: str,
        access_key_id: str,
        secret_access_key: str,
        dvc_bucket: str,
        attachment_bucket: str,
        provisional_retention: timedelta = timedelta(days=30),
        attachment_retention: timedelta = timedelta(days=180),
    ) -> None:
        self._session = session
        self._dvc_bucket = dvc_bucket
        self._attachment_bucket = attachment_bucket
        self._provisional_retention = provisional_retention
        self._attachment_retention = attachment_retention
        self._s3: Any = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name="us-east-1",
        )

    def run(self, now: datetime, *, deletion_limit: int = 1000) -> tuple[int, int]:
        attachments = self._prune_attachments(now, deletion_limit)
        provisional = self._prune_provisional(now, max(0, deletion_limit - attachments))
        self._session.commit()
        return attachments, provisional

    def _prune_attachments(self, now: datetime, limit: int) -> int:
        rows = self._session.scalars(
            select(RunAttachmentRow)
            .where(
                RunAttachmentRow.created_at < now - self._attachment_retention,
                RunAttachmentRow.purged_at.is_(None),
            )
            .order_by(RunAttachmentRow.created_at)
            .limit(limit)
        )
        removed = 0
        for row in rows:
            self._s3.delete_object(Bucket=self._attachment_bucket, Key=row.object_key)
            row.purged_at = now
            removed += 1
        return removed

    def _prune_provisional(self, now: datetime, limit: int) -> int:
        if limit == 0:
            return 0
        protected = self._published_object_keys()
        cutoff = now - self._provisional_retention
        removed = 0
        continuation: str | None = None
        while removed < limit:
            parameters: dict[str, Any] = {
                "Bucket": self._dvc_bucket,
                "Prefix": "dvc/",
                "MaxKeys": min(1000, limit - removed),
            }
            if continuation is not None:
                parameters["ContinuationToken"] = continuation
            page = self._s3.list_objects_v2(**parameters)
            for item in page.get("Contents", []):
                key = str(item["Key"])
                modified = item["LastModified"]
                if key not in protected and modified < cutoff:
                    self._s3.delete_object(Bucket=self._dvc_bucket, Key=key)
                    removed += 1
                    if removed >= limit:
                        break
            if not page.get("IsTruncated") or removed >= limit:
                break
            continuation = str(page["NextContinuationToken"])
        return removed

    def _published_object_keys(self) -> set[str]:
        protected = set(self._session.scalars(select(ArtifactStorageLocationRow.object_key)))
        rows = self._session.execute(
            select(
                ResearchProjectRow.public_id,
                ArtifactVersionRow.algorithm,
                ArtifactVersionFileRow.digest,
            )
            .select_from(ArtifactVersionFileRow)
            .join(
                ArtifactVersionRow,
                ArtifactVersionRow.id == ArtifactVersionFileRow.artifact_version_id,
            )
            .join(
                ResearchProjectRow,
                ResearchProjectRow.id == ArtifactVersionRow.owning_project_id,
            )
            .where(ArtifactVersionFileRow.digest.is_not(None))
        )
        for project_id, algorithm, digest in rows:
            if digest is not None:
                protected.add(f"dvc/{project_id}/files/{algorithm}/{digest[:2]}/{digest[2:]}")
        return protected
