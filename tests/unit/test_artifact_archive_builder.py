from __future__ import annotations

import hashlib
import io
import zipfile
from datetime import UTC, datetime
from typing import Any

from homebrew_mlflow.application import ArtifactArchiveFile, ArtifactArchiveSource
from homebrew_mlflow.domain import (
    ArtifactVersion,
    AvailabilityState,
    DvcOutputIdentity,
    IntegrityState,
    OutputKind,
    PublicId,
    ResourceKind,
)
from homebrew_mlflow.infrastructure import S3ArtifactArchiveBuilder


class Body:
    def __init__(self, value: bytes) -> None:
        self.value = value

    def iter_chunks(self, chunk_size: int):  # type: ignore[no-untyped-def]
        yield from (
            self.value[index : index + chunk_size]
            for index in range(0, len(self.value), chunk_size)
        )


class S3:
    def __init__(self, source: dict[str, bytes]) -> None:
        self.source = source
        self.parts: list[bytes] = []
        self.completed = b""

    def create_multipart_upload(self, **_kwargs: Any) -> dict[str, str]:
        return {"UploadId": "upload"}

    def upload_part(self, **kwargs: Any) -> dict[str, str]:
        self.parts.append(bytes(kwargs["Body"]))
        return {"ETag": str(kwargs["PartNumber"])}

    def complete_multipart_upload(self, **_kwargs: Any) -> None:
        self.completed = b"".join(self.parts)

    def abort_multipart_upload(self, **_kwargs: Any) -> None:
        self.parts.clear()

    def get_object(self, **kwargs: Any) -> dict[str, Body]:
        return {"Body": Body(self.source[kwargs["Key"]])}


def test_builder_streams_verified_files_into_a_readable_zip64_archive() -> None:
    content = b"candidate,sealed_rmsle\n1,0.123\n"
    digest = hashlib.md5(content).hexdigest()  # noqa: S324 - DVC identity algorithm
    version = ArtifactVersion(
        PublicId.generate(ResourceKind.ARTIFACT_VERSION),
        PublicId.generate(ResourceKind.ARTIFACT),
        PublicId.generate(ResourceKind.PROJECT),
        DvcOutputIdentity("md5", digest, OutputKind.FILE, len(content), 1),
        IntegrityState.VERIFIED,
        AvailabilityState.AVAILABLE,
        datetime(2026, 8, 29, tzinfo=UTC),
        sequence=4,
    )
    source = ArtifactArchiveSource(
        version, "sealed predictions", "research",
        (ArtifactArchiveFile("results/predictions.csv", len(content), digest, "source-key"),),
    )
    client = S3({"source-key": content})
    builder = object.__new__(S3ArtifactArchiveBuilder)
    builder._client = client  # type: ignore[attr-defined]
    builder._destination_bucket = "downloads"  # type: ignore[attr-defined]

    built = builder.build(source, lambda _processed: None)

    assert built.size == len(client.completed)
    with zipfile.ZipFile(io.BytesIO(client.completed)) as archive:
        assert archive.namelist() == ["sealed-predictions-v4/results/predictions.csv"]
        assert archive.read(archive.namelist()[0]) == content
