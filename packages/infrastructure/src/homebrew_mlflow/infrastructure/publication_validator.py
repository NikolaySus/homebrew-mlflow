from __future__ import annotations

import json
from hashlib import md5, sha256
from pathlib import PurePosixPath
from time import monotonic
from typing import Any
from urllib.parse import quote
from uuid import UUID

import boto3  # type: ignore[import-untyped]
import httpx
import yaml
from botocore.exceptions import ClientError  # type: ignore[import-untyped]
from homebrew_mlflow.application import (
    PublicationValidationError,
    ValidatedFile,
    ValidatedPublication,
)
from homebrew_mlflow.contracts import MODEL_SIGNATURE_FORMAT, parse_model_signature
from homebrew_mlflow.domain import (
    ArtifactKind,
    DvcOutputIdentity,
    OutputKind,
    PublicationOperation,
    PublicId,
    ResourceKind,
    normalize_artifact_path,
    normalize_file_index,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import ArtifactRow, GitRepositoryRow, ResearchProjectRow, RunRow


class GitLabDvcPublicationValidator:
    def __init__(
        self,
        session: Session,
        *,
        gitlab_url: str,
        gitlab_token: str,
        s3_endpoint_url: str,
        s3_bucket: str,
        s3_access_key_id: str,
        s3_secret_access_key: str,
        max_bytes: int = 100 * 1024**3,
        max_objects: int = 1_000_000,
        max_seconds: int = 30 * 60,
    ) -> None:
        self._session = session
        self._gitlab_url = gitlab_url.rstrip("/")
        self._gitlab_headers = {"PRIVATE-TOKEN": gitlab_token}
        self._bucket = s3_bucket
        self._max_bytes = max_bytes
        self._max_objects = max_objects
        self._max_seconds = max_seconds
        self._verified_bytes = 0
        self._verified_objects = 0
        self._deadline = 0.0
        self._s3: Any = boto3.client(
            "s3",
            endpoint_url=s3_endpoint_url,
            aws_access_key_id=s3_access_key_id,
            aws_secret_access_key=s3_secret_access_key,
            region_name="us-east-1",
        )

    def validate(self, operation: PublicationOperation) -> ValidatedPublication:
        self._verified_bytes = 0
        self._verified_objects = 0
        self._deadline = monotonic() + self._max_seconds
        request = operation.request_payload
        artifact_id, artifact_kind = self._artifact(
            operation.project_id, request.get("artifact_id")
        )
        provider_id = self._repository(operation.project_id, request.get("repository_id"))
        commit_sha = request.get("commit_sha")
        if not isinstance(commit_sha, str) or len(commit_sha) != 40:
            raise PublicationValidationError("commit_not_found")
        self._require_commit(provider_id, commit_sha)
        selector = request.get("selector")
        if not isinstance(selector, dict):
            raise PublicationValidationError("selector_not_found")
        metadata = self._selected_output(provider_id, commit_sha, selector)
        algorithm, raw_digest = self._digest(metadata)
        is_directory = raw_digest.endswith(".dir")
        digest = raw_digest.removesuffix(".dir")
        prefix = f"dvc/{operation.project_id}/files/{algorithm}"
        root_key = f"{prefix}/{digest[:2]}/{digest[2:]}"
        if is_directory:
            root_key += ".dir"
            files = self._directory_files(prefix, root_key, algorithm, digest)
        else:
            size, _ = self._verify_object(root_key, algorithm, digest)
            output_path = self._selector_output(selector)
            files = (ValidatedFile(output_path, size, digest),)
        total_size = sum(item.size for item in files)
        expected_size = metadata.get("size")
        expected_count = metadata.get("nfiles")
        if expected_size is not None and int(expected_size) != total_size:
            raise PublicationValidationError("object_corrupt")
        if expected_count is not None and int(expected_count) != len(files):
            raise PublicationValidationError("object_corrupt")
        run_id = self._run(operation.project_id, request.get("run_id"))
        signature, signature_sha256 = self._model_signature(
            artifact_kind, provider_id, commit_sha, request.get("model_signature")
        )
        return ValidatedPublication(
            artifact_id=artifact_id,
            identity=DvcOutputIdentity(
                algorithm,
                digest,
                OutputKind.DIRECTORY if is_directory else OutputKind.FILE,
                total_size,
                len(files),
            ),
            files=files,
            bucket=self._bucket,
            object_key=root_key,
            producing_run_id=run_id,
            model_signature=signature,
            model_signature_sha256=signature_sha256,
        )

    def _artifact(self, project_id: PublicId, value: object) -> tuple[PublicId, ArtifactKind]:
        try:
            artifact_id = PublicId(ResourceKind.ARTIFACT, str(value))
        except ValueError as error:
            raise PublicationValidationError("artifact_not_found") from error
        project_key = self._project_key(project_id)
        kind = self._session.scalar(
            select(ArtifactRow.kind).where(
                ArtifactRow.public_id == str(artifact_id),
                ArtifactRow.owning_project_id == project_key,
            )
        )
        if kind is None:
            raise PublicationValidationError("artifact_not_found")
        return artifact_id, ArtifactKind(kind)

    def _model_signature(
        self,
        artifact_kind: ArtifactKind,
        provider_id: str,
        commit_sha: str,
        value: object,
    ) -> tuple[dict[str, Any] | None, str | None]:
        if artifact_kind is not ArtifactKind.MODEL:
            if value is not None:
                raise PublicationValidationError("model_signature_not_allowed")
            return None, None
        if not isinstance(value, dict):
            raise PublicationValidationError("model_signature_required")
        if value.get("format") != MODEL_SIGNATURE_FORMAT or not isinstance(
            value.get("path"), str
        ):
            raise PublicationValidationError("model_signature_invalid")
        try:
            content = self._file(
                provider_id,
                commit_sha,
                value["path"],
                not_found_code="model_signature_not_found",
            )
            return parse_model_signature(content)
        except PublicationValidationError:
            raise
        except ValueError as error:
            raise PublicationValidationError("model_signature_invalid") from error

    def _repository(self, project_id: PublicId, value: object) -> str:
        project_key = self._project_key(project_id)
        row = self._session.scalar(
            select(GitRepositoryRow).where(
                GitRepositoryRow.public_id == str(value),
                GitRepositoryRow.project_id == project_key,
                GitRepositoryRow.state == "active",
            )
        )
        if row is None or row.provider_id is None:
            raise PublicationValidationError("repository_not_found")
        return row.provider_id

    def _project_key(self, project_id: PublicId) -> UUID:
        key = self._session.scalar(
            select(ResearchProjectRow.id).where(ResearchProjectRow.public_id == str(project_id))
        )
        if key is None:
            raise PublicationValidationError("forbidden")
        return key

    def _run(self, project_id: PublicId, value: object) -> PublicId | None:
        if value is None:
            return None
        try:
            run_id = PublicId(ResourceKind.RUN, str(value))
        except ValueError as error:
            raise PublicationValidationError("run_not_found") from error
        run = self._session.execute(
            select(RunRow.id, RunRow.provenance_status).where(
                RunRow.public_id == str(run_id),
                RunRow.project_id == self._project_key(project_id),
            )
        ).one_or_none()
        if run is None:
            raise PublicationValidationError("run_not_found")
        if run.provenance_status != "complete":
            raise PublicationValidationError("run_provenance_incomplete")
        return run_id

    def _require_commit(self, provider_id: str, commit_sha: str) -> None:
        url = (
            f"{self._gitlab_url}/api/v4/projects/{quote(provider_id, safe='')}/"
            f"repository/commits/{commit_sha}"
        )
        response = httpx.get(url, headers=self._gitlab_headers, timeout=20)
        if response.status_code == 404:
            raise PublicationValidationError("commit_not_found")
        response.raise_for_status()

    def _file(
        self,
        provider_id: str,
        commit_sha: str,
        path: str,
        *,
        not_found_code: str = "selector_not_found",
    ) -> bytes:
        try:
            safe_path = normalize_artifact_path(path)
        except ValueError as error:
            raise PublicationValidationError("unsafe_path") from error
        url = (
            f"{self._gitlab_url}/api/v4/projects/{quote(provider_id, safe='')}/repository/"
            f"files/{quote(safe_path, safe='')}/raw"
        )
        response = httpx.get(
            url, headers=self._gitlab_headers, params={"ref": commit_sha}, timeout=20
        )
        if response.status_code == 404:
            raise PublicationValidationError(not_found_code)
        response.raise_for_status()
        return response.content

    def _selected_output(
        self, provider_id: str, commit_sha: str, selector: dict[str, Any]
    ) -> dict[str, Any]:
        try:
            if selector.get("kind") == "pipeline-output":
                lock_path = str(PurePosixPath(str(selector["pipeline_file"])).with_name("dvc.lock"))
                document = yaml.safe_load(self._file(provider_id, commit_sha, lock_path))
                outputs = document["stages"][selector["stage"]]["outs"]
            elif selector.get("kind") == "standalone-output":
                document = yaml.safe_load(
                    self._file(provider_id, commit_sha, str(selector["dvc_file"]))
                )
                outputs = document["outs"]
            else:
                raise KeyError
            target = normalize_artifact_path(str(selector["output"]))
            for output in outputs:
                if normalize_artifact_path(str(output["path"])) == target:
                    return dict(output)
        except (KeyError, TypeError, ValueError, yaml.YAMLError) as error:
            raise PublicationValidationError("unsupported_dvc_metadata") from error
        raise PublicationValidationError("selector_not_found")

    @staticmethod
    def _selector_output(selector: dict[str, Any]) -> str:
        try:
            return normalize_artifact_path(str(selector["output"]))
        except (KeyError, ValueError) as error:
            raise PublicationValidationError("unsafe_path") from error

    @staticmethod
    def _digest(metadata: dict[str, Any]) -> tuple[str, str]:
        for algorithm in ("md5", "sha256"):
            value = metadata.get(algorithm)
            if isinstance(value, str):
                return algorithm, value
        raise PublicationValidationError("unsupported_dvc_metadata")

    def _verify_object(
        self, key: str, algorithm: str, expected_digest: str, *, capture: bool = False
    ) -> tuple[int, bytes | None]:
        if monotonic() >= self._deadline:
            raise PublicationValidationError("worker_limit_exceeded")
        if self._verified_objects >= self._max_objects + 1:
            raise PublicationValidationError("worker_limit_exceeded")
        try:
            response = self._s3.get_object(Bucket=self._bucket, Key=key)
        except ClientError as error:
            if error.response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 404:
                raise PublicationValidationError("object_missing") from error
            raise PublicationValidationError("storage_unavailable") from error
        hasher = md5(usedforsecurity=False) if algorithm == "md5" else sha256()
        captured = bytearray() if capture else None
        size = 0
        try:
            for chunk in response["Body"].iter_chunks(chunk_size=1024 * 1024):
                if monotonic() >= self._deadline:
                    raise PublicationValidationError("worker_limit_exceeded")
                size += len(chunk)
                if self._verified_bytes + size > self._max_bytes:
                    raise PublicationValidationError("worker_limit_exceeded")
                hasher.update(chunk)
                if captured is not None:
                    captured.extend(chunk)
        except PublicationValidationError:
            raise
        except Exception as error:
            raise PublicationValidationError("storage_unavailable") from error
        if hasher.hexdigest() != expected_digest:
            raise PublicationValidationError("digest_mismatch")
        self._verified_bytes += size
        self._verified_objects += 1
        return size, bytes(captured) if captured is not None else None

    def _directory_files(
        self, prefix: str, manifest_key: str, algorithm: str, digest: str
    ) -> tuple[ValidatedFile, ...]:
        _, body = self._verify_object(manifest_key, algorithm, digest, capture=True)
        try:
            manifest = json.loads(body or b"")
            if not isinstance(manifest, list) or not manifest:
                raise ValueError("directory manifest must be a non-empty list")
            if len(manifest) > self._max_objects:
                raise PublicationValidationError("worker_limit_exceeded")
        except PublicationValidationError:
            raise
        except (TypeError, ValueError) as error:
            raise PublicationValidationError("directory_graph_invalid") from error
        files: list[ValidatedFile] = []
        try:
            for item in manifest:
                path = normalize_artifact_path(item["relpath"])
                child_digest = item[algorithm]
                key = f"{prefix}/{child_digest[:2]}/{child_digest[2:]}"
                size, _ = self._verify_object(key, algorithm, child_digest)
                if item.get("size") is not None and int(item["size"]) != size:
                    raise PublicationValidationError("object_corrupt")
                files.append(ValidatedFile(path, size, child_digest))
            normalize_file_index([item.path for item in files])
        except PublicationValidationError:
            raise
        except (KeyError, TypeError, ValueError) as error:
            raise PublicationValidationError("directory_graph_invalid") from error
        return tuple(files)
