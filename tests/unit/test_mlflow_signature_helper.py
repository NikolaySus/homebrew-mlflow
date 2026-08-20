import json

import pandas as pd
from homebrew_mlflow.mlflow_plugins.signature import write_model_signature
from mlflow.models import infer_signature


def test_signature_helper_writes_portable_inputs_and_outputs(tmp_path) -> None:  # type: ignore[no-untyped-def]
    signature = infer_signature(
        pd.DataFrame({"age": [20.0], "active": [True]}),
        pd.DataFrame({"score": [0.8]}),
    )
    target = write_model_signature(tmp_path / "model-signature.json", signature)
    document = json.loads(target.read_text())
    assert document["schema_version"] == 1
    assert [value["name"] for value in document["inputs"]] == ["age", "active"]
    assert [value["name"] for value in document["outputs"]] == ["score"]
