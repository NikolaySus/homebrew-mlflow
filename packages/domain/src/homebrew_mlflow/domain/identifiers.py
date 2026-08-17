from __future__ import annotations

import re
import secrets
import time
from dataclasses import dataclass
from enum import StrEnum


class ResourceKind(StrEnum):
    ORGANIZATION = "org"
    PROJECT = "pr"
    REPOSITORY = "repo"
    EXPERIMENT = "exp"
    RUN = "run"
    ARTIFACT = "ar"
    ARTIFACT_VERSION = "av"
    PIPELINE_DEFINITION = "pipeline"
    PIPELINE_VERSION = "pv"
    ENVIRONMENT_SPECIFICATION = "env"
    PUBLICATION = "pub"
    SHARING_GRANT = "grant"
    SHARED_REFERENCE = "ref"
    DERIVATION = "derivation"
    MACHINE_CREDENTIAL = "machine"
    PRINCIPAL = "principal"
    REQUEST = "req"


_PUBLIC_ID = re.compile(r"^(?P<prefix>[a-z]+)_(?P<value>[0-9A-HJKMNP-TV-Z]{26})$")
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _new_ulid() -> str:
    value = (int(time.time_ns() // 1_000_000) << 80) | int.from_bytes(
        secrets.token_bytes(10), "big"
    )
    encoded = ["0"] * 26
    for position in range(25, -1, -1):
        encoded[position] = _CROCKFORD[value & 31]
        value >>= 5
    return "".join(encoded)


@dataclass(frozen=True, slots=True)
class PublicId:
    kind: ResourceKind
    value: str

    def __post_init__(self) -> None:
        match = _PUBLIC_ID.fullmatch(self.value)
        if match is None or match.group("prefix") != self.kind.value:
            raise ValueError(f"invalid {self.kind.value} public identifier")

    def __str__(self) -> str:
        return self.value

    @classmethod
    def generate(cls, kind: ResourceKind) -> PublicId:
        return cls(kind=kind, value=f"{kind.value}_{_new_ulid()}")
