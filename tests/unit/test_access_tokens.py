from datetime import UTC, datetime, timedelta

import pytest
from homebrew_mlflow.application import (
    AccessTokenFailure,
    AccessTokenService,
    TokenAudience,
)
from homebrew_mlflow.domain import MachineScope, PublicId, ResourceKind

NOW = datetime(2026, 8, 17, 12, tzinfo=UTC)
KEY = "a-development-signing-key-with-at-least-32-bytes"


def test_access_token_round_trip_preserves_audience_project_and_scopes() -> None:
    service = AccessTokenService(KEY, "https://ml.example")
    principal_id = PublicId.generate(ResourceKind.PRINCIPAL)
    project_id = PublicId.generate(ResourceKind.PROJECT)

    token = service.issue(
        principal_id,
        TokenAudience.DVC_CREDENTIALS,
        project_id=project_id,
        scopes=frozenset({MachineScope.DVC_TRANSFER}),
        now=NOW,
    )
    claims = service.verify(token, TokenAudience.DVC_CREDENTIALS, now=NOW)

    assert claims.principal_id == principal_id
    assert claims.project_id == project_id
    assert claims.scopes == frozenset({MachineScope.DVC_TRANSFER})


def test_mlflow_access_token_is_bound_to_one_run() -> None:
    service = AccessTokenService(KEY, "https://ml.example")
    principal_id = PublicId.generate(ResourceKind.PRINCIPAL)
    project_id = PublicId.generate(ResourceKind.PROJECT)
    run_id = PublicId.generate(ResourceKind.RUN)

    token = service.issue(
        principal_id,
        TokenAudience.MLFLOW,
        project_id=project_id,
        run_id=run_id,
        scopes=frozenset({MachineScope.TRACK}),
        now=NOW,
        lifetime=timedelta(hours=12),
    )
    claims = service.verify(token, TokenAudience.MLFLOW, now=NOW + timedelta(hours=11))

    assert claims.run_id == run_id
    assert claims.project_id == project_id


def test_access_token_rejects_wrong_audience_and_expiry() -> None:
    service = AccessTokenService(KEY, "https://ml.example")
    token = service.issue(
        PublicId.generate(ResourceKind.PRINCIPAL), TokenAudience.PLATFORM_API, now=NOW
    )

    with pytest.raises(AccessTokenFailure, match="audience"):
        service.verify(token, TokenAudience.MLFLOW, now=NOW)
    with pytest.raises(AccessTokenFailure, match="expired"):
        service.verify(token, TokenAudience.PLATFORM_API, now=NOW + timedelta(minutes=21))


def test_access_token_rejects_tampering() -> None:
    service = AccessTokenService(KEY, "https://ml.example")
    token = service.issue(
        PublicId.generate(ResourceKind.PRINCIPAL), TokenAudience.PLATFORM_API, now=NOW
    )
    parts = token.split(".")
    parts[1] = parts[1][:-1] + ("A" if parts[1][-1] != "A" else "B")

    with pytest.raises(AccessTokenFailure, match="signature"):
        service.verify(".".join(parts), TokenAudience.PLATFORM_API, now=NOW)
