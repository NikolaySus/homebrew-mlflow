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
    SecretContextRow,
)
from .infisical_auth import InfisicalAccessToken, infisical_authorization_headers


class InfisicalMembershipReconciler:
    """Synchronize canonical project membership without ever reading secret values."""

    _ROLE_SLUG = {"viewer": "viewer", "contributor": "member", "maintainer": "admin"}

    def __init__(
        self,
        session: Session,
        *,
        base_url: str,
        access_token: InfisicalAccessToken,
        target_interval: timedelta = timedelta(minutes=5),
    ) -> None:
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._access_token = access_token
        self._target_interval = target_interval

    def run_once(self, now: datetime) -> bool:
        stale_before = now - self._target_interval
        context = self._session.scalar(
            select(SecretContextRow)
            .where(
                (SecretContextRow.last_reconciled_at.is_(None))
                | (SecretContextRow.last_reconciled_at < stale_before)
            )
            .order_by(SecretContextRow.updated_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if context is None:
            return False
        context.reconcile_attempt += 1
        try:
            changes = self._reconcile(context)
        except Exception as error:
            context.reconciliation_state = "failed"
            context.last_error_code = type(error).__name__
            self._audit(context, now, "failed", {"error_code": type(error).__name__})
        else:
            context.reconciliation_state = "in_sync"
            context.last_error_code = None
            if changes:
                self._audit(context, now, "success", {"changes": changes})
        context.last_reconciled_at = now
        self._session.commit()
        return True

    def _reconcile(self, context: SecretContextRow) -> int:
        expected_rows = self._session.execute(
            select(GitLabIdentityBindingRow.email, ProjectMembershipRow.role)
            .join(
                ProjectMembershipRow,
                ProjectMembershipRow.principal_id == GitLabIdentityBindingRow.principal_id,
            )
            .where(ProjectMembershipRow.project_id == context.project_id)
        ).all()
        if any(email is None for email, _role in expected_rows):
            raise RuntimeError("GitLab identity email has not been synchronized")
        expected = {email: self._ROLE_SLUG[role] for email, role in expected_rows}
        managed_usernames = set(
            self._session.scalars(
                select(GitLabIdentityBindingRow.email).where(
                    GitLabIdentityBindingRow.email.is_not(None)
                )
            )
        )
        endpoint = f"{self._base_url}/api/v1/projects/{context.infisical_project_id}/memberships"
        response = httpx.get(
            endpoint,
            headers=infisical_authorization_headers(self._access_token),
            timeout=20,
        )
        response.raise_for_status()
        memberships = response.json().get("memberships", [])
        actual: dict[str, tuple[str, str | None]] = {}
        changes = 0
        for membership in memberships:
            user = membership.get("user", {})
            roles = membership.get("roles", [])
            username = user.get("username")
            if isinstance(username, str):
                role = roles[0].get("role") if roles and isinstance(roles[0], dict) else None
                actual[username] = (str(membership.get("id")), role)
        for role in sorted(set(expected.values())):
            missing = sorted(
                username
                for username, expected_role in expected.items()
                if expected_role == role and username not in actual
            )
            if missing:
                invited = httpx.post(
                    endpoint,
                    headers=infisical_authorization_headers(self._access_token),
                    json={"emails": missing, "usernames": [], "roleSlugs": [role]},
                    timeout=20,
                )
                invited.raise_for_status()
                changes += len(missing)
        extra = sorted((set(actual) & managed_usernames) - set(expected))
        if extra:
            removed = httpx.request(
                "DELETE",
                endpoint,
                headers=infisical_authorization_headers(self._access_token),
                json={"emails": extra, "usernames": []},
                timeout=20,
            )
            removed.raise_for_status()
            changes += len(extra)
        for username in sorted(set(expected) & set(actual)):
            membership_id, actual_role = actual[username]
            if actual_role != expected[username]:
                updated = httpx.patch(
                    f"{endpoint}/{membership_id}",
                    headers=infisical_authorization_headers(self._access_token),
                    json={"roles": [{"role": expected[username], "isTemporary": False}]},
                    timeout=20,
                )
                updated.raise_for_status()
                changes += 1
        return changes

    def _audit(
        self,
        context: SecretContextRow,
        now: datetime,
        outcome: str,
        metadata: dict[str, object],
    ) -> None:
        self._session.add(
            AuditEventRow(
                occurred_at=now,
                actor_principal_id=None,
                project_id=context.project_id,
                action="infisical_membership.reconcile",
                resource_type="secret_context",
                resource_id=context.infisical_project_id,
                outcome=outcome,
                request_id=str(PublicId.generate(ResourceKind.REQUEST)),
                safe_metadata=metadata,
            )
        )
