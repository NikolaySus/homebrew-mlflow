from __future__ import annotations

import json
from datetime import UTC
from typing import Any

import boto3  # type: ignore[import-untyped]
from botocore.exceptions import ClientError  # type: ignore[import-untyped]
from homebrew_mlflow.application import DvcNamespace, TemporaryS3Credential
from homebrew_mlflow.domain import PublicId


class MinioDvcCredentialIssuer:
    def __init__(
        self,
        endpoint_url: str,
        remote_base_url: str,
        access_key_id: str,
        secret_access_key: str,
    ) -> None:
        self._endpoint_url = endpoint_url
        self._namespace = DvcNamespace.parse(remote_base_url)
        self._bucket = self._namespace.bucket
        self._access_key_id = access_key_id
        self._secret_access_key = secret_access_key
        s3: Any = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name="us-east-1",
        )
        try:
            s3.head_bucket(Bucket=self._bucket)
        except ClientError as error:
            if error.response.get("ResponseMetadata", {}).get("HTTPStatusCode") != 404:
                raise
            s3.create_bucket(Bucket=self._bucket)

    def issue(
        self, project_id: PublicId, read_only_object_keys: tuple[str, ...]
    ) -> TemporaryS3Credential:
        prefix = self._namespace.project_prefix(project_id)
        list_prefixes = [prefix, f"{prefix}/*", *read_only_object_keys]
        policy: dict[str, Any] = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": ["s3:ListBucket"],
                    "Resource": [f"arn:aws:s3:::{self._bucket}"],
                    "Condition": {"StringLike": {"s3:prefix": list_prefixes}},
                },
                {
                    "Effect": "Allow",
                    "Action": ["s3:GetObject", "s3:PutObject"],
                    "Resource": [f"arn:aws:s3:::{self._bucket}/{prefix}/*"],
                },
            ],
        }
        if read_only_object_keys:
            policy["Statement"].append(
                {
                    "Effect": "Allow",
                    "Action": ["s3:GetObject"],
                    "Resource": [
                        f"arn:aws:s3:::{self._bucket}/{key}"
                        for key in read_only_object_keys
                    ],
                }
            )
        sts: Any = boto3.client(
            "sts",
            endpoint_url=self._endpoint_url,
            aws_access_key_id=self._access_key_id,
            aws_secret_access_key=self._secret_access_key,
            region_name="us-east-1",
        )
        response = sts.assume_role(
            RoleArn="arn:aws:iam::000000000000:role/homebrew-dvc",
            RoleSessionName=f"dvc-{project_id}",
            DurationSeconds=900,
            Policy=json.dumps(policy, separators=(",", ":")),
        )
        credentials = response["Credentials"]
        expiration = credentials["Expiration"]
        if expiration.tzinfo is None:
            expiration = expiration.replace(tzinfo=UTC)
        return TemporaryS3Credential(
            credentials["AccessKeyId"],
            credentials["SecretAccessKey"],
            credentials["SessionToken"],
            expiration,
        )
