from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RetentionDependencies:
    retained_runs: int = 0
    shared_references: int = 0
    derivatives: int = 0
    active_grants: int = 0
    replicas: int = 0
    legal_hold: bool = False

    def __post_init__(self) -> None:
        if any(
            value < 0
            for value in (
                self.retained_runs,
                self.shared_references,
                self.derivatives,
                self.active_grants,
                self.replicas,
            )
        ):
            raise ValueError("retention dependency counts cannot be negative")

    @property
    def blockers(self) -> tuple[str, ...]:
        values: list[str] = []
        if self.retained_runs:
            values.append("retained_runs")
        if self.shared_references:
            values.append("shared_references")
        if self.derivatives:
            values.append("derivatives")
        if self.active_grants:
            values.append("active_grants")
        if self.replicas:
            values.append("replicas")
        if self.legal_hold:
            values.append("legal_hold")
        return tuple(values)


@dataclass(frozen=True, slots=True)
class PublishedRetentionPolicy:
    retain_indefinitely: bool = True

    def permits_physical_purge(self, dependencies: RetentionDependencies) -> bool:
        return not self.retain_indefinitely and not dependencies.blockers
