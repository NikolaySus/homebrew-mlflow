"""Minimal framework-independent autologging setup without model binary uploads."""

from __future__ import annotations

import mlflow


def enable_autologging() -> None:
    mlflow.autolog(log_models=False)
