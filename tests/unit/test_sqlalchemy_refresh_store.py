from datetime import UTC, datetime
from uuid import uuid4

import pytest
from homebrew_mlflow.application import (
    RefreshCredentialService,
    RefreshFailure,
    RefreshReuseDetected,
)
from homebrew_mlflow.domain import Principal, PrincipalKind
from homebrew_mlflow.infrastructure.database import (
    Base,
    PrincipalRow,
    SqlAlchemyRefreshCredentialStore,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


def test_sql_store_rotates_atomically_and_revokes_on_reuse() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    principal = Principal.create(PrincipalKind.HUMAN, "Researcher")
    now = datetime(2026, 8, 13, tzinfo=UTC)

    with Session(engine) as session:
        session.add(
            PrincipalRow(
                id=uuid4(),
                public_id=str(principal.id),
                kind=principal.kind.value,
                display_name=principal.display_name,
                created_at=now,
                archived_at=None,
            )
        )
        session.commit()
        service = RefreshCredentialService(SqlAlchemyRefreshCredentialStore(session))
        first = service.issue(principal.id, now)
        second = service.rotate(first, now)

        with pytest.raises(RefreshReuseDetected):
            service.rotate(first, now)
        with pytest.raises(RefreshFailure):
            service.rotate(second, now)
