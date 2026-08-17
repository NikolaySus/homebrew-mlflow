from __future__ import annotations

from typing import Any


def build_tracking_store(store_uri: str, artifact_uri: str | None = None) -> Any:
    """Load the store after MLflow finishes populating its entry-point registry."""
    from .tracking_store import HomebrewTrackingStore

    return HomebrewTrackingStore(store_uri, artifact_uri)  # type: ignore[abstract]
