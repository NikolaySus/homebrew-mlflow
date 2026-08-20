from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

from flask import Flask
from homebrew_mlflow.mlflow_plugins.model_artifacts import HomebrewModelArtifactRepository
from mlflow.models import Model


class Response:
    ok = True
    status_code = 200

    def json(self) -> dict[str, Any]:
        return {
            "artifacts": [
                {
                    "id": "ar_01K00000000000000000000000",
                    "name": "ranker",
                    "kind": "model",
                    "versions": [
                        {
                            "id": "av_01K00000000000000000000000",
                            "algorithm": "md5",
                            "digest": "a" * 32,
                            "mlflow_model_id": "m-0123456789abcdef0123456789abcdef",
                            "producing_run_id": "run_01K00000000000000000000000",
                            "model_signature": {
                                "schema_version": 1,
                                "inputs": [
                                    {"name": "age", "type": "double", "required": True}
                                ],
                                "outputs": [
                                    {"name": "score", "type": "float", "required": True}
                                ],
                            },
                        }
                    ],
                }
            ]
        }

    def raise_for_status(self) -> None:
        return None


def _token() -> str:
    payload = base64.urlsafe_b64encode(json.dumps({"scp": ["read"]}).encode()).rstrip(b"=")
    return f"header.{payload.decode()}.signature"


def test_virtual_mlmodel_contains_only_signature_and_dvc_provenance(
    monkeypatch, tmp_path: Path
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("HOMEBREW_MLFLOW_PLATFORM_INTERNAL_URL", "http://api:8000")
    monkeypatch.setattr(
        "homebrew_mlflow.mlflow_plugins.model_artifacts.requests.get",
        lambda *_args, **_kwargs: Response(),
    )
    repository = HomebrewModelArtifactRepository(
        "homebrew-model://pr-01k00000000000000000000000/"
        "av_01K00000000000000000000000"
    )
    app = Flask(__name__)
    with app.test_request_context(headers={"Authorization": f"Bearer {_token()}"}):
        downloaded = repository.download_artifacts("MLmodel", str(tmp_path))

    model = Model.load(downloaded)
    assert model.signature is not None
    assert model.signature.inputs.input_names() == ["age"]
    assert model.signature.outputs.input_names() == ["score"]
    assert set(model.flavors) == {"homebrew_dvc"}
    assert "model.pkl" not in Path(downloaded).read_text()
