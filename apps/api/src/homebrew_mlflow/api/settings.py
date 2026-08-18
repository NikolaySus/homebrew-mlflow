from datetime import timedelta
from functools import lru_cache
from pathlib import Path

from pydantic import AnyHttpUrl, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HOMEBREW_MLFLOW_", extra="ignore")

    environment: str = "development"
    public_base_url: AnyHttpUrl = AnyHttpUrl("http://localhost:8080")
    database_url: str = (
        "postgresql+psycopg://homebrew_mlflow:homebrew_mlflow@localhost:5432/homebrew_mlflow"
    )
    client_recommended_version: str = "0.2.2"
    client_compatible_versions: str = ">=0.2,<0.3"
    client_requires_python: str = ">=3.11"
    client_platforms: list[str] = Field(default_factory=lambda: ["linux", "macos", "windows"])
    client_release_manifest: Path | None = None
    gitlab_base_url: AnyHttpUrl = AnyHttpUrl("http://gitlab")
    gitlab_public_base_url: AnyHttpUrl = AnyHttpUrl("http://git.localhost:8080")
    gitlab_oauth_client_id: str = "development-client"
    gitlab_oauth_client_id_file: Path | None = None
    gitlab_oauth_client_secret: SecretStr = SecretStr("development-client-secret")
    gitlab_oauth_client_secret_file: Path | None = None
    gitlab_device_oauth_client_id: str = "development-device-client"
    gitlab_device_oauth_client_id_file: Path | None = None
    gitlab_device_oauth_client_secret: SecretStr = SecretStr("development-device-client-secret")
    gitlab_device_oauth_client_secret_file: Path | None = None
    gitlab_integration_token: SecretStr = SecretStr("development-integration-token")
    gitlab_integration_token_file: Path | None = None
    infisical_base_url: AnyHttpUrl = AnyHttpUrl("http://infisical:8080")
    infisical_reconciliation_token: SecretStr = SecretStr("development-infisical-token")
    infisical_reconciliation_token_file: Path | None = None
    access_token_signing_key: SecretStr = SecretStr(
        "development-access-token-signing-key-change-before-production"
    )
    access_token_key_id: str = "development-v1"
    bootstrap_token: SecretStr = SecretStr("development-one-time-bootstrap-token")
    run_logging_token_hours: int = Field(default=12, ge=1, le=24)
    s3_endpoint_url: str = "http://object-storage:9000"
    s3_public_endpoint_url: AnyHttpUrl = AnyHttpUrl("http://localhost:9000")
    s3_access_key_id: str = "development"
    s3_secret_access_key: SecretStr = SecretStr("development-only-password")
    attachment_bucket: str = "homebrew-mlflow"
    dvc_bucket: str = "research"
    dvc_remote_base_url: str = "s3://research/dvc"
    sse_retention_days: int = Field(default=7, ge=1, le=30)
    publication_max_seconds: int = Field(default=1800, ge=60, le=1800)
    publication_max_bytes: int = Field(default=100 * 1024**3, ge=1, le=100 * 1024**3)
    publication_max_objects: int = Field(default=1_000_000, ge=1, le=1_000_000)
    attachment_max_file_bytes: int = Field(default=50 * 1024**2, ge=1)
    attachment_max_run_bytes: int = Field(default=250 * 1024**2, ge=1)
    attachment_max_count: int = Field(default=1000, ge=1)
    attachment_retention_days: int = Field(default=180, ge=1)
    provisional_retention_days: int = Field(default=30, ge=1)
    reconciliation_target_seconds: int = Field(default=300, ge=30)
    reconciliation_alert_seconds: int = Field(default=900, ge=60)
    backup_policy_reference: str = "docs/operations/backup-and-restore.md"

    @property
    def run_logging_token_lifetime(self) -> timedelta:
        return timedelta(hours=self.run_logging_token_hours)

    @model_validator(mode="after")
    def reject_insecure_production_defaults(self) -> "Settings":
        if self.gitlab_oauth_client_id_file is not None:
            self.gitlab_oauth_client_id = self._read_secret_file(
                self.gitlab_oauth_client_id_file
            )
        if self.gitlab_device_oauth_client_id_file is not None:
            self.gitlab_device_oauth_client_id = self._read_secret_file(
                self.gitlab_device_oauth_client_id_file
            )
        for file_field, value_field in (
            (self.gitlab_oauth_client_secret_file, "gitlab_oauth_client_secret"),
            (
                self.gitlab_device_oauth_client_secret_file,
                "gitlab_device_oauth_client_secret",
            ),
            (self.gitlab_integration_token_file, "gitlab_integration_token"),
            (self.infisical_reconciliation_token_file, "infisical_reconciliation_token"),
        ):
            if file_field is not None:
                setattr(self, value_field, SecretStr(self._read_secret_file(file_field)))
        if self.environment == "production":
            if self.public_base_url.scheme != "https":
                raise ValueError("production public URL must use HTTPS")
            if self.gitlab_public_base_url.scheme != "https":
                raise ValueError("production public GitLab URL must use HTTPS")
            if self.access_token_key_id == "development-v1":
                raise ValueError("production requires an explicit access-token key ID")
            if self.gitlab_oauth_client_id == "development-client":
                raise ValueError("production requires explicit GitLab OAuth coordinates")
            if self.gitlab_device_oauth_client_id == "development-device-client":
                raise ValueError("production requires explicit GitLab device OAuth coordinates")
            insecure = {
                self.access_token_signing_key.get_secret_value(),
                self.gitlab_oauth_client_secret.get_secret_value(),
                self.gitlab_device_oauth_client_secret.get_secret_value(),
                self.gitlab_integration_token.get_secret_value(),
                self.infisical_reconciliation_token.get_secret_value(),
                self.bootstrap_token.get_secret_value(),
                self.s3_secret_access_key.get_secret_value(),
            }
            if any(value.startswith("development") for value in insecure):
                raise ValueError("production requires external non-development secret values")
        if self.reconciliation_alert_seconds < self.reconciliation_target_seconds:
            raise ValueError("reconciliation alert threshold must not precede its target")
        if self.attachment_max_run_bytes < self.attachment_max_file_bytes:
            raise ValueError("Run attachment quota must be at least the per-file limit")
        from homebrew_mlflow.application import DvcNamespace

        if DvcNamespace.parse(self.dvc_remote_base_url).bucket != self.dvc_bucket:
            raise ValueError("DVC remote base URL bucket must match dvc_bucket")
        return self

    @staticmethod
    def _read_secret_file(path: Path) -> str:
        value = path.read_text(encoding="utf-8").strip()
        if not value:
            raise ValueError(f"secret file is empty: {path}")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
