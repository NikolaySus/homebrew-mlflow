"""Replace this deterministic starter with the project's training code."""

from __future__ import annotations

import json
from pathlib import Path

from examples.mlflow_autolog import enable_autologging


def main() -> None:
    enable_autologging()
    Path("models").mkdir(exist_ok=True)
    Path("models/model.json").write_text('{"status":"starter"}\n', encoding="utf-8")
    Path("metrics.json").write_text(json.dumps({"starter": 1.0}) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
