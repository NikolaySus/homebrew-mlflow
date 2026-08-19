from datetime import UTC, datetime, timedelta

import pytest
import yaml
from homebrew_mlflow.application import DvcPointer
from homebrew_mlflow.domain import (
    Artifact,
    ArtifactKind,
    ArtifactSharingGrant,
    ArtifactVersion,
    AvailabilityState,
    DvcOutputIdentity,
    IntegrityState,
    InvalidPublicationTransition,
    OutputKind,
    PublicationState,
    PublicId,
    ResourceKind,
    UnsafeArtifactPath,
    normalize_artifact_alias,
    normalize_artifact_path,
    normalize_file_index,
    transition_publication,
)


def test_artifact_kind_description_and_alias_rules_are_explicit() -> None:
    artifact = Artifact.create(
        PublicId.generate(ResourceKind.PROJECT),
        "training-data",
        datetime(2026, 1, 1, tzinfo=UTC),
        ArtifactKind.DATASET,
        "  Curated inputs  ",
    )

    assert artifact.kind is ArtifactKind.DATASET
    assert artifact.description == "Curated inputs"
    assert normalize_artifact_alias("candidate_2") == "candidate_2"
    for reserved in ("latest", "LATEST", "v7", "V10"):
        with pytest.raises(ValueError, match="reserved"):
            normalize_artifact_alias(reserved)


def test_dvc_identity_is_algorithm_qualified_and_complete() -> None:
    identity = DvcOutputIdentity("md5", "a" * 32, OutputKind.FILE, 12, 1)
    assert identity.digest == "a" * 32
    with pytest.raises(ValueError, match="exactly one"):
        DvcOutputIdentity("md5", "a" * 32, OutputKind.FILE, 12, 2)


@pytest.mark.parametrize("path", ["../secret", "/absolute", "a\\b", "", "a/../b"])
def test_unsafe_artifact_paths_are_rejected(path: str) -> None:
    with pytest.raises(UnsafeArtifactPath):
        normalize_artifact_path(path)


def test_unicode_duplicate_paths_are_rejected_after_normalization() -> None:
    with pytest.raises(UnsafeArtifactPath, match="duplicate"):
        normalize_file_index(["café.txt", "cafe\u0301.txt"])


def test_publication_is_terminal_after_publish() -> None:
    state = transition_publication(PublicationState.QUEUED, PublicationState.RESOLVING)
    state = transition_publication(state, PublicationState.VERIFYING)
    state = transition_publication(state, PublicationState.COMMITTING)
    state = transition_publication(state, PublicationState.PUBLISHED)
    with pytest.raises(InvalidPublicationTransition):
        transition_publication(state, PublicationState.FAILED)


def test_sharing_revocation_is_prospective_with_completed_run_exception() -> None:
    effective = datetime(2026, 1, 1, tzinfo=UTC)
    revoked = effective + timedelta(days=10)
    grant = ArtifactSharingGrant(
        PublicId.generate(ResourceKind.SHARING_GRANT),
        PublicId.generate(ResourceKind.ARTIFACT_VERSION),
        PublicId.generate(ResourceKind.PROJECT),
        PublicId.generate(ResourceKind.PROJECT),
        effective,
        effective,
        PublicId.generate(ResourceKind.PRINCIPAL),
        revoked,
    )
    assert not grant.permits_new_use(revoked)
    assert grant.permits_completed_run_recovery(revoked - timedelta(seconds=1))
    assert not grant.permits_completed_run_recovery(revoked + timedelta(seconds=1))


def test_exact_version_pointer_is_standard_dvc_metadata() -> None:
    version = ArtifactVersion(
        PublicId.generate(ResourceKind.ARTIFACT_VERSION),
        PublicId.generate(ResourceKind.ARTIFACT),
        PublicId.generate(ResourceKind.PROJECT),
        DvcOutputIdentity("md5", "a" * 32, OutputKind.DIRECTORY, 42, 2),
        IntegrityState.VERIFIED,
        AvailabilityState.AVAILABLE,
        datetime(2026, 1, 1, tzinfo=UTC),
    )

    document = yaml.safe_load(DvcPointer(version, "models/final").content())

    assert document == {
        "outs": [
            {
                "md5": f"{'a' * 32}.dir",
                "hash": "md5",
                "size": 42,
                "nfiles": 2,
                "path": "models/final",
            }
        ]
    }
