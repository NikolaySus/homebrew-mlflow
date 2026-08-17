from __future__ import annotations

from datetime import datetime, timedelta

import httpx
from homebrew_mlflow.domain import PublicId, ResourceKind
from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import (
    AuditEventRow,
    GitLabIdentityBindingRow,
    ProjectMembershipRow,
    ResearchProjectRow,
)


class GitLabMembershipReconciler:
    """Converge direct GitLab group access on canonical human project membership."""

    _ACCESS_LEVEL = {"viewer": 20, "contributor": 30, "maintainer": 40}

    def __init__(
        self,
        session: Session,
        *,
        base_url: str,
        access_token: str,
        target_interval: timedelta = timedelta(minutes=5),
    ) -> None:
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._headers = {"PRIVATE-TOKEN": access_token}
        self._target_interval = target_interval

    def run_once(self, now: datetime) -> bool:
        stale_before = now - self._target_interval
        project = self._session.scalar(
            select(ResearchProjectRow)
            .where(
                ResearchProjectRow.state == "active",
                ResearchProjectRow.gitlab_namespace_id.is_not(None),
                (ResearchProjectRow.gitlab_reconciliation_state != "in_sync")
                | (ResearchProjectRow.gitlab_reconciled_at.is_(None))
                | (ResearchProjectRow.gitlab_reconciled_at < stale_before),
            )
            .order_by(ResearchProjectRow.updated_at, ResearchProjectRow.public_id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if project is None or project.gitlab_namespace_id is None:
            return False
        project.gitlab_reconcile_attempt += 1
        try:
            changes = self._reconcile(project)
        except Exception as error:
            project.gitlab_reconciliation_state = "failed"
            project.gitlab_reconciliation_error = type(error).__name__
            self._audit(project, now, "failed", {"error_code": type(error).__name__})
        else:
            project.gitlab_reconciliation_state = "in_sync"
            project.gitlab_reconciliation_error = None
            if changes:
                self._audit(project, now, "success", {"changes": changes})
        project.gitlab_reconciled_at = now
        self._session.commit()
        return True

    def _reconcile(self, project: ResearchProjectRow) -> int:
        expected_rows = self._session.execute(
            select(GitLabIdentityBindingRow.subject, ProjectMembershipRow.role)
            .join(
                ProjectMembershipRow,
                ProjectMembershipRow.principal_id == GitLabIdentityBindingRow.principal_id,
            )
            .where(ProjectMembershipRow.project_id == project.id)
        ).all()
        expected = {str(subject): self._ACCESS_LEVEL[role] for subject, role in expected_rows}
        managed_subjects = set(self._session.scalars(select(GitLabIdentityBindingRow.subject)))
        endpoint = (
            f"{self._base_url}/api/v4/groups/{project.gitlab_namespace_id}/members"
        )
        page = "1"
        actual: dict[str, int] = {}
        while page:
            response = httpx.get(
                endpoint,
                headers=self._headers,
                params={"per_page": 100, "page": page},
                timeout=20,
            )
            response.raise_for_status()
            actual.update(
                {
                    str(item["id"]): int(item["access_level"])
                    for item in response.json()
                    if "id" in item and "access_level" in item
                }
            )
            page = response.headers.get("X-Next-Page", "")
        changes = 0
        for subject in sorted(set(expected) - set(actual)):
            created = httpx.post(
                endpoint,
                headers=self._headers,
                data={"user_id": subject, "access_level": expected[subject]},
                timeout=20,
            )
            created.raise_for_status()
            changes += 1
        for subject in sorted(set(expected) & set(actual)):
            if actual[subject] != expected[subject]:
                updated = httpx.put(
                    f"{endpoint}/{subject}",
                    headers=self._headers,
                    data={"access_level": expected[subject]},
                    timeout=20,
                )
                updated.raise_for_status()
                changes += 1
        for subject in sorted((set(actual) & managed_subjects) - set(expected)):
            removed = httpx.delete(
                f"{endpoint}/{subject}", headers=self._headers, timeout=20
            )
            removed.raise_for_status()
            changes += 1
        return changes

    def _audit(
        self,
        project: ResearchProjectRow,
        now: datetime,
        outcome: str,
        metadata: dict[str, object],
    ) -> None:
        self._session.add(
            AuditEventRow(
                occurred_at=now,
                actor_principal_id=None,
                project_id=project.id,
                action="gitlab_membership.reconcile",
                resource_type="research_project",
                resource_id=project.public_id,
                outcome=outcome,
                request_id=str(PublicId.generate(ResourceKind.REQUEST)),
                safe_metadata=metadata,
            )
        )
