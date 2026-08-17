from __future__ import annotations

import os

from mlflow.tracking.context.abstract_context import RunContextProvider


class HomebrewRunContextProvider(RunContextProvider):
    _VARIABLES = {
        "HOMEBREW_MLFLOW_PROJECT_ID": "homebrew.project_id",
        "HOMEBREW_MLFLOW_REPOSITORY_ID": "homebrew.repository_id",
        "HOMEBREW_MLFLOW_RUN_ID": "homebrew.run_id",
        "HOMEBREW_MLFLOW_GIT_COMMIT": "homebrew.git_commit",
    }

    def in_context(self) -> bool:
        return all(
            os.getenv(name) for name in self._VARIABLES if name != "HOMEBREW_MLFLOW_GIT_COMMIT"
        )

    def tags(self) -> dict[str, str]:
        return {
            tag: value
            for variable, tag in self._VARIABLES.items()
            if (value := os.getenv(variable)) is not None
        }
