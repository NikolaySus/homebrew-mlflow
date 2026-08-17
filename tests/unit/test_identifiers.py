import pytest
from homebrew_mlflow.domain import PublicId, ResourceKind


def test_public_id_requires_matching_prefix_and_ulid_shape() -> None:
    identifier = PublicId(ResourceKind.RUN, "run_01ARZ3NDEKTSV4RRFFQ69G5FAV")
    assert str(identifier) == "run_01ARZ3NDEKTSV4RRFFQ69G5FAV"

    with pytest.raises(ValueError, match="invalid run"):
        PublicId(ResourceKind.RUN, "exp_01ARZ3NDEKTSV4RRFFQ69G5FAV")
