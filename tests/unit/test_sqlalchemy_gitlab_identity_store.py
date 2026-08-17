from datetime import UTC, datetime, timedelta

from homebrew_mlflow.infrastructure.database import (
    Base,
    GitLabIdentityBindingRow,
    PrincipalRow,
    SqlAlchemyGitLabIdentityStore,
)
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session


def test_gitlab_subject_is_immutable_identity_and_profile_is_refreshed() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    now = datetime(2026, 8, 17, tzinfo=UTC)

    with Session(engine) as session:
        store = SqlAlchemyGitLabIdentityStore(session)
        first = store.resolve_or_create("100", "researcher", "Researcher", now)
        second = store.resolve_or_create(
            "100", "renamed", "Renamed Researcher", now + timedelta(hours=1)
        )

        assert second.id == first.id
        assert second.display_name == "Renamed Researcher"
        assert session.scalar(select(func.count()).select_from(PrincipalRow)) == 1
        binding = session.scalar(select(GitLabIdentityBindingRow))
        assert binding is not None
        assert binding.username == "renamed"
        assert binding.last_seen_at.replace(tzinfo=UTC) == now + timedelta(hours=1)
