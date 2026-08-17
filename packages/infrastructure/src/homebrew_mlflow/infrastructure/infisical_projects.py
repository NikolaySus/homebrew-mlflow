from __future__ import annotations

from datetime import datetime
from typing import Any, cast

import httpx
from homebrew_mlflow.domain import PublicId, ResourceKind
from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from .database import AuditEventRow, ResearchProjectRow, SecretContextRow


class InfisicalProjectProvisioner:
    """Create one deterministic Infisical secret-manager project per research project."""

    def __init__(self, session: Session, *, base_url: str, access_token: str) -> None:
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {access_token}"}

    def run_once(self, now: datetime) -> bool:
        project = self._session.scalar(
            select(ResearchProjectRow)
            .where(
                ResearchProjectRow.state == "active",
                ~exists().where(SecretContextRow.project_id == ResearchProjectRow.id),
            )
            .order_by(ResearchProjectRow.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if project is None:
            return False
        slug = self._slug(project.slug, project.public_id)
        try:
            infisical_project = self._find(slug) or self._create(project.name, slug)
            identifier = infisical_project.get("id") or infisical_project.get("_id")
            if not isinstance(identifier, str) or not identifier:
                raise RuntimeError("Infisical returned a project without an ID")
            environments = infisical_project.get("environments", [])
            environment = next(
                (
                    value.get("slug")
                    for value in environments
                    if isinstance(value, dict) and value.get("slug") == "dev"
                ),
                None,
            )
            if not isinstance(environment, str):
                raise RuntimeError("Infisical project did not create the dev environment")
            self._session.add(
                SecretContextRow(
                    project_id=project.id,
                    infisical_project_id=identifier,
                    environment_slug=environment,
                    secret_path="/",
                    updated_at=now,
                    reconciliation_state="queued",
                    last_error_code=None,
                    last_reconciled_at=None,
                    reconcile_attempt=0,
                )
            )
            self._audit(project, identifier, now, "success", {})
        except Exception as error:
            self._audit(
                project,
                project.public_id,
                now,
                "failed",
                {"error_code": type(error).__name__},
            )
        self._session.commit()
        return True

    def _find(self, slug: str) -> dict[str, Any] | None:
        response = httpx.get(
            f"{self._base_url}/api/v1/projects",
            headers=self._headers,
            params={"type": "secret-manager"},
            timeout=20,
        )
        response.raise_for_status()
        projects = response.json().get("projects", [])
        return next(
            (
                cast(dict[str, Any], value)
                for value in projects
                if isinstance(value, dict) and value.get("slug") == slug
            ),
            None,
        )

    def _create(self, name: str, slug: str) -> dict[str, Any]:
        response = httpx.post(
            f"{self._base_url}/api/v1/projects",
            headers=self._headers,
            json={
                "projectName": name[:64],
                "projectDescription": "Managed by Homebrew MLflow",
                "slug": slug,
                "type": "secret-manager",
                "template": "default",
                "shouldCreateDefaultEnvs": True,
                "hasDeleteProtection": True,
            },
            timeout=20,
        )
        response.raise_for_status()
        value = response.json().get("project")
        if not isinstance(value, dict):
            raise RuntimeError("Infisical returned an invalid create-project response")
        return cast(dict[str, Any], value)

    @staticmethod
    def _slug(project_slug: str, public_id: str) -> str:
        suffix = public_id.rsplit("_", 1)[-1][-8:].lower()
        return f"hm-{project_slug[:22].strip('-')}-{suffix}"[:36]

    def _audit(
        self,
        project: ResearchProjectRow,
        resource_id: str,
        now: datetime,
        outcome: str,
        metadata: dict[str, str],
    ) -> None:
        self._session.add(
            AuditEventRow(
                occurred_at=now,
                actor_principal_id=None,
                project_id=project.id,
                action="infisical_project.provision",
                resource_type="secret_context",
                resource_id=resource_id,
                outcome=outcome,
                request_id=str(PublicId.generate(ResourceKind.REQUEST)),
                safe_metadata=metadata,
            )
        )
