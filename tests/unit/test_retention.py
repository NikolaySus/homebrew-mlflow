from homebrew_mlflow.application import PublishedRetentionPolicy, RetentionDependencies


def test_published_versions_are_indefinite_under_v1_deployment_policy() -> None:
    assert not PublishedRetentionPolicy().permits_physical_purge(RetentionDependencies())


def test_configurable_purge_still_requires_an_empty_dependency_graph() -> None:
    policy = PublishedRetentionPolicy(retain_indefinitely=False)

    assert policy.permits_physical_purge(RetentionDependencies())
    dependencies = RetentionDependencies(
        retained_runs=1, shared_references=1, derivatives=1, active_grants=1
    )
    assert not policy.permits_physical_purge(dependencies)
    assert dependencies.blockers == (
        "retained_runs",
        "shared_references",
        "derivatives",
        "active_grants",
    )
