from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_model_signature(path: str | Path, signature: Any) -> Path:
    """Write an MLflow ModelSignature as a portable publication sidecar."""

    inputs = getattr(signature, "inputs", None)
    outputs = getattr(signature, "outputs", None)
    if inputs is None or outputs is None:
        raise ValueError("signature must define both inputs and outputs")
    document = {
        "schema_version": 1,
        "inputs": json.loads(inputs.to_json()),
        "outputs": json.loads(outputs.to_json()),
    }
    target = Path(path)
    target.write_text(
        json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return target
