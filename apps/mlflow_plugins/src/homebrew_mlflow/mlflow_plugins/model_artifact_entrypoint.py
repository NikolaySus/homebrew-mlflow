from __future__ import annotations

from typing import Any


def build_model_artifact_repository(
    artifact_uri: str,
    tracking_uri: str | None = None,
    registry_uri: str | None = None,
) -> Any:
    from .model_artifacts import HomebrewModelArtifactRepository

    return HomebrewModelArtifactRepository(artifact_uri, tracking_uri, registry_uri)
