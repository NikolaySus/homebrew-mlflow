from __future__ import annotations

from datetime import datetime
from typing import Protocol

from homebrew_mlflow.domain import ProjectRole, PublicId, SecretContext

from .projects import AuthorizationDenied


class SecretContextUnitOfWork(Protocol):
    def project_role(self, project_id: PublicId, principal_id: PublicId) -> ProjectRole | None: ...

    def secret_context(self, project_id: PublicId) -> SecretContext | None: ...

    def save_secret_context(self, context: SecretContext) -> None: ...

    def commit(self) -> None: ...


class SecretContextService:
    def __init__(self, unit_of_work: SecretContextUnitOfWork) -> None:
        self._uow = unit_of_work

    def get(self, actor_id: PublicId, project_id: PublicId) -> SecretContext | None:
        if self._uow.project_role(project_id, actor_id) is None:
            raise AuthorizationDenied("project membership is required")
        return self._uow.secret_context(project_id)

    def configure(
        self,
        actor_id: PublicId,
        project_id: PublicId,
        infisical_project_id: str,
        environment_slug: str,
        secret_path: str,
        now: datetime,
    ) -> SecretContext:
        if self._uow.project_role(project_id, actor_id) is not ProjectRole.MAINTAINER:
            raise AuthorizationDenied("Maintainer role is required to configure Infisical")
        context = SecretContext(
            project_id,
            infisical_project_id.strip(),
            environment_slug.strip(),
            "/" + secret_path.strip().strip("/"),
            now,
        )
        self._uow.save_secret_context(context)
        self._uow.commit()
        return context
