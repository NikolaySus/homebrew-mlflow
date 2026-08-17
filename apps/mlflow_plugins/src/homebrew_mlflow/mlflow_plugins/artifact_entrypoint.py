from __future__ import annotations

from typing import Any


def build_artifact_repository(
    artifact_uri: str,
    tracking_uri: str | None = None,
    registry_uri: str | None = None,
) -> Any:
    """Load the repository after MLflow finishes populating its entry-point registry."""
    from .artifacts import HomebrewArtifactRepository

    return HomebrewArtifactRepository(artifact_uri, tracking_uri, registry_uri)
