from __future__ import annotations

from typing import Any


def build_workspace_store(workspace_uri: str) -> Any:
    from .workspace_store import HomebrewWorkspaceStore

    return HomebrewWorkspaceStore(workspace_uri)
