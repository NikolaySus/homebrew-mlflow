from __future__ import annotations

import json
from importlib.resources import files
from typing import Any, cast


def load_openapi() -> dict[str, Any]:
    resource = files("homebrew_mlflow.contracts").joinpath("openapi.json")
    return cast(dict[str, Any], json.loads(resource.read_text(encoding="utf-8")))
