from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from .identifiers import PublicId, ResourceKind


def _validate_key(key: str) -> str:
    normalized = key.strip()
    if not normalized or len(normalized) > 250:
        raise ValueError("tracking key must contain between 1 and 250 characters")
    if normalized.startswith("homebrew."):
        raise ValueError("homebrew.* tracking keys are reserved by the platform")
    return normalized


@dataclass(frozen=True, slots=True)
class RunParameter:
    run_id: PublicId
    key: str
    value: str
    logged_at: datetime

    def __post_init__(self) -> None:
        if self.run_id.kind is not ResourceKind.RUN:
            raise ValueError("parameter must belong to a Run")
        object.__setattr__(self, "key", _validate_key(self.key))
        if len(self.value) > 6000:
            raise ValueError("parameter value exceeds 6000 characters")


@dataclass(frozen=True, slots=True)
class RunMetric:
    run_id: PublicId
    key: str
    value: float
    timestamp_ms: int
    step: int
    logged_at: datetime

    def __post_init__(self) -> None:
        if self.run_id.kind is not ResourceKind.RUN:
            raise ValueError("metric must belong to a Run")
        object.__setattr__(self, "key", _validate_key(self.key))
        if not math.isfinite(self.value):
            raise ValueError("metric value must be finite")
        if self.timestamp_ms < 0 or self.step < 0:
            raise ValueError("metric timestamp and step must be non-negative")


@dataclass(frozen=True, slots=True)
class RunTag:
    run_id: PublicId
    key: str
    value: str
    updated_at: datetime

    def __post_init__(self) -> None:
        if self.run_id.kind is not ResourceKind.RUN:
            raise ValueError("tag must belong to a Run")
        object.__setattr__(self, "key", _validate_key(self.key))
        if len(self.value) > 8000:
            raise ValueError("tag value exceeds 8000 characters")
