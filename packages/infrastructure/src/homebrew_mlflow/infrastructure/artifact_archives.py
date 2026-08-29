from __future__ import annotations

import hashlib
import io
import re
import zipfile
from collections.abc import Callable
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import quote

import boto3  # type: ignore[import-untyped]
from homebrew_mlflow.application import ArtifactArchiveSource, BuiltArtifactArchive


class ArchiveBuildError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _MultipartWriter(io.RawIOBase):
    def __init__(self, client: Any, bucket: str, key: str, part_size: int = 8 * 1024**2) -> None:
        self.client, self.bucket, self.key, self.part_size = client, bucket, key, part_size
        self.upload_id = client.create_multipart_upload(
            Bucket=bucket, Key=key, ContentType="application/zip"
        )["UploadId"]
        self.buffer = bytearray()
        self.parts: list[dict[str, object]] = []
        self.position = 0

    def writable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return False

    def tell(self) -> int:
        return self.position

    def write(self, value: Any) -> int:
        data = bytes(value)
        self.buffer.extend(data)
        self.position += len(data)
        while len(self.buffer) >= self.part_size:
            self._upload(bytes(self.buffer[: self.part_size]))
            del self.buffer[: self.part_size]
        return len(data)

    def _upload(self, body: bytes) -> None:
        number = len(self.parts) + 1
        result = self.client.upload_part(
            Bucket=self.bucket,
            Key=self.key,
            UploadId=self.upload_id,
            PartNumber=number,
            Body=body,
        )
        self.parts.append({"ETag": result["ETag"], "PartNumber": number})

    def finish(self) -> int:
        if self.buffer or not self.parts:
            self._upload(bytes(self.buffer))
            self.buffer.clear()
        self.client.complete_multipart_upload(
            Bucket=self.bucket,
            Key=self.key,
            UploadId=self.upload_id,
            MultipartUpload={"Parts": self.parts},
        )
        return self.position

    def abort(self) -> None:
        self.client.abort_multipart_upload(
            Bucket=self.bucket, Key=self.key, UploadId=self.upload_id
        )


def archive_filename(artifact_name: str, sequence: int) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", artifact_name).strip(".-") or "artifact"
    return f"{stem}-v{sequence}.zip"


class S3ArtifactArchiveBuilder:
    def __init__(
        self,
        *,
        endpoint_url: str,
        access_key_id: str,
        secret_access_key: str,
        destination_bucket: str,
    ) -> None:
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
        )
        self._destination_bucket = destination_bucket

    def build(
        self, source: ArtifactArchiveSource, progress: Callable[[int], None]
    ) -> BuiltArtifactArchive:
        key = f"artifact-download-archives/{source.version.id}/{source.version.identity.digest}.zip"
        writer = _MultipartWriter(self._client, self._destination_bucket, key)
        processed = 0
        root = PurePosixPath(
            PurePosixPath(archive_filename(source.artifact_name, source.version.sequence)).stem
        )
        try:
            with zipfile.ZipFile(
                writer, "w", compression=zipfile.ZIP_STORED, allowZip64=True
            ) as archive:
                for item in source.files:
                    relative = PurePosixPath(item.path.replace("\\", "/"))
                    if relative.is_absolute() or ".." in relative.parts:
                        raise ArchiveBuildError("invalid_artifact_path")
                    digest = hashlib.new(source.version.identity.algorithm)
                    size = 0
                    response = self._client.get_object(Bucket=source.bucket, Key=item.object_key)
                    with archive.open(str(root / relative), "w", force_zip64=True) as target:
                        for chunk in response["Body"].iter_chunks(chunk_size=1024**2):
                            if not chunk:
                                continue
                            target.write(chunk)
                            digest.update(chunk)
                            size += len(chunk)
                            processed += len(chunk)
                            progress(processed)
                    if size != item.size or digest.hexdigest() != item.digest:
                        raise ArchiveBuildError("source_integrity_mismatch")
            size = writer.finish()
            return BuiltArtifactArchive(key, size)
        except Exception:
            writer.abort()
            raise

    def delete(self, object_key: str) -> None:
        self._client.delete_object(Bucket=self._destination_bucket, Key=object_key)

    def presigned_download(self, object_key: str, filename: str, expires_seconds: int) -> str:
        disposition = f"attachment; filename*=UTF-8''{quote(filename)}"
        return str(
            self._client.generate_presigned_url(
                "get_object",
                Params={
                    "Bucket": self._destination_bucket,
                    "Key": object_key,
                    "ResponseContentDisposition": disposition,
                },
                ExpiresIn=expires_seconds,
            )
        )
