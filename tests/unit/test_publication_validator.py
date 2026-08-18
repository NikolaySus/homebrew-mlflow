from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from time import monotonic
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from homebrew_mlflow.application import PublicationValidationError
from homebrew_mlflow.domain import PublicId, ResourceKind
from homebrew_mlflow.infrastructure import GitLabDvcPublicationValidator


@dataclass
class Body:
    value: bytes

    def iter_chunks(self, chunk_size: int) -> list[bytes]:
        return [
            self.value[offset : offset + chunk_size]
            for offset in range(0, len(self.value), chunk_size)
        ]


@dataclass
class S3:
    objects: dict[str, bytes]

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:  # noqa: N803
        return {"Body": Body(self.objects[Key])}


def validator(objects: dict[str, bytes], *, max_bytes: int = 1024) -> GitLabDvcPublicationValidator:
    result = object.__new__(GitLabDvcPublicationValidator)
    result._s3 = S3(objects)  # type: ignore[attr-defined]
    result._bucket = "research"  # type: ignore[attr-defined]
    result._max_bytes = max_bytes  # type: ignore[attr-defined]
    result._max_objects = 100  # type: ignore[attr-defined]
    result._max_seconds = 60  # type: ignore[attr-defined]
    result._verified_bytes = 0  # type: ignore[attr-defined]
    result._verified_objects = 0  # type: ignore[attr-defined]
    result._deadline = monotonic() + 60  # type: ignore[attr-defined]
    return result


def md5_digest(value: bytes) -> str:
    return hashlib.md5(value, usedforsecurity=False).hexdigest()


def test_incomplete_run_cannot_be_used_for_publication() -> None:
    class Result:
        def one_or_none(self) -> SimpleNamespace:
            return SimpleNamespace(id=uuid4(), provenance_status="incomplete")

    class Session:
        def scalar(self, _statement: object) -> object:
            return uuid4()

        def execute(self, _statement: object) -> Result:
            return Result()

    instance = object.__new__(GitLabDvcPublicationValidator)
    instance._session = Session()  # type: ignore[attr-defined]

    with pytest.raises(PublicationValidationError, match="run_provenance_incomplete"):
        instance._run(
            PublicId.generate(ResourceKind.PROJECT),
            str(PublicId.generate(ResourceKind.RUN)),
        )


def test_file_object_is_streamed_and_rehashed() -> None:
    content = b"verified model bytes"
    digest = md5_digest(content)
    instance = validator({"object": content})

    size, captured = instance._verify_object("object", "md5", digest)

    assert size == len(content)
    assert captured is None


def test_digest_mismatch_is_stable_failure() -> None:
    instance = validator({"object": b"corrupt"})

    with pytest.raises(PublicationValidationError, match="digest_mismatch"):
        instance._verify_object("object", "md5", "0" * 32)


def test_directory_manifest_and_every_child_are_rehashed() -> None:
    first = b"one"
    second = b"two"
    first_digest = md5_digest(first)
    second_digest = md5_digest(second)
    manifest = json.dumps(
        [
            {"md5": first_digest, "relpath": "a.txt", "size": len(first)},
            {"md5": second_digest, "relpath": "nested/b.txt", "size": len(second)},
        ],
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    manifest_digest = md5_digest(manifest)
    prefix = "dvc/pr/files/md5"
    objects = {
        "manifest.dir": manifest,
        f"{prefix}/{first_digest[:2]}/{first_digest[2:]}": first,
        f"{prefix}/{second_digest[:2]}/{second_digest[2:]}": second,
    }

    files = validator(objects)._directory_files(
        prefix, "manifest.dir", "md5", manifest_digest
    )

    assert [(item.path, item.size) for item in files] == [
        ("a.txt", 3),
        ("nested/b.txt", 3),
    ]


def test_worker_byte_limit_is_enforced_while_streaming() -> None:
    content = b"too large"
    instance = validator({"object": content}, max_bytes=3)

    with pytest.raises(PublicationValidationError, match="worker_limit_exceeded"):
        instance._verify_object("object", "md5", md5_digest(content))
