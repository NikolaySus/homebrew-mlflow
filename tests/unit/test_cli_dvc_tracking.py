from homebrew_mlflow.cli.main import _dvc_tracking_values, _find_dvc_experiment


def test_exact_dvc_revision_and_collision_safe_tracking_values() -> None:
    payload = {
        "workspace": {
            "baseline": {"rev": "base", "data": {"metrics": {}}},
            "candidate": {
                "rev": "experiment-revision",
                "data": {
                    "metrics": {
                        "scores.json": {"data": {"accuracy": 0.9, "nested": {"loss": 0.1}}},
                        "holdout.json": {"data": {"accuracy": 0.8}},
                    }
                },
            },
        }
    }

    selected = _find_dvc_experiment(payload, "experiment-revision")
    assert selected is not None
    metrics, warnings = _dvc_tracking_values(selected["metrics"], metrics=True)

    assert metrics == [
        {"key": "holdout.json:accuracy", "value": 0.8},
        {"key": "scores.json:accuracy", "value": 0.9},
        {"key": "nested.loss", "value": 0.1},
    ]
    assert warnings == []


def test_dvc_capture_skips_non_scalar_and_non_finite_values() -> None:
    values, warnings = _dvc_tracking_values(
        {
            "metrics.json": {
                "data": {"valid": 1, "boolean": True, "invalid": float("inf")}
            }
        },
        metrics=True,
    )

    assert values == [{"key": "valid", "value": 1.0}]
    assert warnings == ["Skipped non-finite metric invalid"]
