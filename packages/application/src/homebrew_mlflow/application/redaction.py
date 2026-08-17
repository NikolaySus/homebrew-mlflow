from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

_SENSITIVE_KEY = re.compile(
    r"(?:authorization|cookie|token|secret|password|access[_-]?key|api[_-]?key|credential)",
    re.IGNORECASE,
)


def redact_mapping(
    values: Mapping[str, Any], configured_secret_names: frozenset[str] = frozenset()
) -> dict[str, Any]:
    names = {name.casefold() for name in configured_secret_names}
    result: dict[str, Any] = {}
    for key, value in values.items():
        if _SENSITIVE_KEY.search(key) or key.casefold() in names:
            result[key] = "[REDACTED]"
        elif isinstance(value, Mapping):
            result[key] = redact_mapping(value, configured_secret_names)
        elif isinstance(value, list):
            result[key] = [
                redact_mapping(item, configured_secret_names) if isinstance(item, Mapping) else item
                for item in value
            ]
        else:
            result[key] = value
    return result
