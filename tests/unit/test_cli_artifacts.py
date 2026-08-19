from typing import Any

import pytest
import typer
from homebrew_mlflow.cli.main import _preflight_run_inputs, _warn_if_generic_artifact


class Response:
    def __init__(self, payload: dict[str, Any], status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code

    def json(self) -> dict[str, Any]:
        return self.payload

    def raise_for_status(self) -> None:
        return None


class Client:
    def __init__(self, response: Response) -> None:
        self.response = response

    def get(self, *_args: Any, **_kwargs: Any) -> Response:
        return self.response


def test_generic_publication_warning_explains_mlflow_visibility(capsys) -> None:  # type: ignore[no-untyped-def]
    _warn_if_generic_artifact(
        {
            "id": "ar_01K00000000000000000000000",
            "name": "trained-model",
            "kind": "generic",
        }
    )

    assert "classified as dataset or model" in capsys.readouterr().err


def test_typed_artifact_does_not_warn(capsys) -> None:  # type: ignore[no-untyped-def]
    _warn_if_generic_artifact(
        {
            "id": "ar_01K00000000000000000000000",
            "name": "trained-model",
            "kind": "model",
        }
    )

    assert capsys.readouterr().err == ""


def test_run_input_preflight_rejects_unavailable_version() -> None:
    with pytest.raises(typer.BadParameter, match="not verified and available"):
        _preflight_run_inputs(
            Client(Response({"integrity": "verified", "availability": "missing"})),  # type: ignore[arg-type]
            "https://ml.example",
            {"Authorization": "Bearer token"},
            ["av_01K00000000000000000000000"],
        )
