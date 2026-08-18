from __future__ import annotations

import os

import boto3  # type: ignore[import-untyped]
import pytest
from botocore.exceptions import ClientError  # type: ignore[import-untyped]
from homebrew_mlflow.domain import PublicId, ResourceKind
from homebrew_mlflow.infrastructure import MinioDvcCredentialIssuer


def test_temporary_dvc_credentials_are_confined_to_one_project() -> None:
    endpoint = os.getenv("HOMEBREW_MLFLOW_TEST_S3_ENDPOINT")
    access_key = os.getenv("HOMEBREW_MLFLOW_TEST_S3_ACCESS_KEY_ID")
    secret_key = os.getenv("HOMEBREW_MLFLOW_TEST_S3_SECRET_ACCESS_KEY")
    if not endpoint or not access_key or not secret_key:
        pytest.skip("real S3-compatible boundary credentials are not configured")
    project = PublicId.generate(ResourceKind.PROJECT)
    other_project = PublicId.generate(ResourceKind.PROJECT)
    credential = MinioDvcCredentialIssuer(
        endpoint, "s3://research/dvc", access_key, secret_key
    ).issue(project, ())
    administrator = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="us-east-1",
    )
    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=credential.access_key_id,
        aws_secret_access_key=credential.secret_access_key,
        aws_session_token=credential.session_token,
        region_name="us-east-1",
    )
    own_key = f"dvc/{project}/files/md5/00/test-object"
    try:
        client.put_object(Bucket="research", Key=own_key, Body=b"boundary-test")
        assert client.get_object(Bucket="research", Key=own_key)["Body"].read() == b"boundary-test"
        client.list_objects_v2(Bucket="research", Prefix=f"dvc/{project}/", MaxKeys=1)

        with pytest.raises(ClientError):
            client.list_objects_v2(Bucket="research", Prefix=f"dvc/{other_project}/", MaxKeys=1)
        with pytest.raises(ClientError):
            client.put_object(
                Bucket="research", Key=f"dvc/{other_project}/files/md5/00/test", Body=b"denied"
            )
        with pytest.raises(ClientError):
            client.delete_object(Bucket="research", Key=own_key)
    finally:
        administrator.delete_object(Bucket="research", Key=own_key)
