import pytest
from homebrew_mlflow.application import DvcNamespace
from homebrew_mlflow.domain import PublicId, ResourceKind


def test_project_remote_and_policy_prefix_share_one_namespace() -> None:
    project = PublicId.generate(ResourceKind.PROJECT)
    namespace = DvcNamespace.parse("s3://research/dvc/")

    assert namespace.bucket == "research"
    assert namespace.project_prefix(project) == f"dvc/{project}"
    assert namespace.project_remote_url(project) == f"s3://research/dvc/{project}"


@pytest.mark.parametrize(
    "value",
    ("https://research/dvc", "s3:///dvc", "s3://research/dvc/../shared"),
)
def test_invalid_namespace_is_rejected(value: str) -> None:
    with pytest.raises(ValueError):
        DvcNamespace.parse(value)
