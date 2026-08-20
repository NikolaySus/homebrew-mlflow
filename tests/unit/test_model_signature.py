import json

import pytest
from homebrew_mlflow.contracts import parse_model_signature


def _parse(value: object) -> tuple[dict[str, object], str]:
    return parse_model_signature(json.dumps(value).encode())


def test_column_signature_is_canonicalized() -> None:
    signature, digest = _parse(
        {
            "schema_version": 1,
            "inputs": [{"name": "age", "type": "double", "required": True}],
            "outputs": [{"name": "score", "type": "float", "required": True}],
        }
    )
    assert signature["inputs"][0]["name"] == "age"  # type: ignore[index]
    assert len(digest) == 64


def test_tensor_signature_is_supported() -> None:
    signature, _ = _parse(
        {
            "schema_version": 1,
            "inputs": [
                {"type": "tensor", "tensor-spec": {"dtype": "float32", "shape": [-1, 8]}}
            ],
            "outputs": [
                {"type": "tensor", "tensor-spec": {"dtype": "float32", "shape": [-1, 1]}}
            ],
        }
    )
    assert signature["schema_version"] == 1


@pytest.mark.parametrize(
    "items",
    [
        [],
        [{"type": "unknown", "name": "x"}],
        [
            {"type": "double", "name": "x"},
            {"type": "tensor", "name": "y", "tensor-spec": {"dtype": "float32", "shape": [-1]}},
        ],
    ],
)
def test_invalid_input_schema_is_rejected(items: list[dict[str, object]]) -> None:
    with pytest.raises(ValueError, match="invalid model signature"):
        _parse({"schema_version": 1, "inputs": items, "outputs": [{"type": "double"}]})


def test_oversized_signature_is_rejected() -> None:
    with pytest.raises(ValueError, match="64 KiB"):
        parse_model_signature(b" " * (64 * 1024 + 1))
