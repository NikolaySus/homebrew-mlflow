from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import httpx
from homebrew_mlflow.infrastructure.database import (
    AuditEventRow,
    ResearchProjectRow,
    SecretContextRow,
)
from homebrew_mlflow.infrastructure.infisical_projects import InfisicalProjectProvisioner

NOW = datetime(2026, 8, 18, 12, tzinfo=UTC)


class Session:
    def __init__(self, project: ResearchProjectRow) -> None:
        self.project = project
        self.added: list[object] = []
        self.commits = 0

    def scalar(self, _statement):  # type: ignore[no-untyped-def]
        value, self.project = self.project, None  # type: ignore[assignment]
        return value

    def add(self, value: object) -> None:
        self.added.append(value)

    def commit(self) -> None:
        self.commits += 1


def response(method: str, url: str, payload: object) -> httpx.Response:
    return httpx.Response(200, json=payload, request=httpx.Request(method, url))


def test_missing_project_gets_deterministic_infisical_context(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    project = ResearchProjectRow(
        id=uuid4(),
        public_id="project_01K00000000000000000000",
        organization_id=uuid4(),
        name="Protein Folding",
        slug="protein-folding",
        created_at=NOW,
        archived_at=None,
        state="active",
        gitlab_namespace_id="1",
        failure_code=None,
        updated_at=NOW,
        claimed_at=None,
        claimed_by=None,
        provisioning_attempt=0,
    )
    session = Session(project)
    created: list[dict[str, object]] = []
    authorizations: list[str] = []
    tokens = iter(("first-token", "second-token"))

    def list_projects(url: str, **kwargs: object) -> httpx.Response:
        authorizations.append(kwargs["headers"]["Authorization"])  # type: ignore[index]
        return response("GET", url, {"projects": []})

    monkeypatch.setattr(
        "homebrew_mlflow.infrastructure.infisical_projects.httpx.get",
        list_projects,
    )

    def create(url: str, **kwargs: object) -> httpx.Response:
        authorizations.append(kwargs["headers"]["Authorization"])  # type: ignore[index]
        created.append(kwargs["json"])  # type: ignore[arg-type]
        return response(
            "POST",
            url,
            {
                "project": {
                    "id": "infisical-project",
                    "slug": created[0]["slug"],
                    "environments": [{"name": "Development", "slug": "dev"}],
                }
            },
        )

    monkeypatch.setattr(
        "homebrew_mlflow.infrastructure.infisical_projects.httpx.post", create
    )

    worked = InfisicalProjectProvisioner(  # type: ignore[arg-type]
        session, base_url="https://secrets.example", access_token=lambda: next(tokens)
    ).run_once(NOW)

    context = next(value for value in session.added if isinstance(value, SecretContextRow))
    audit = next(value for value in session.added if isinstance(value, AuditEventRow))
    assert worked
    assert context.infisical_project_id == "infisical-project"
    assert context.environment_slug == "dev"
    assert context.reconciliation_state == "queued"
    assert created[0]["hasDeleteProtection"] is True
    assert created[0]["slug"] == "hm-protein-folding-00000000"
    assert audit.outcome == "success"
    assert authorizations == ["Bearer first-token", "Bearer second-token"]
    assert session.commits == 1
