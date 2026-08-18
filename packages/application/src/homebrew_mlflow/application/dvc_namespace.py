from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from homebrew_mlflow.domain import PublicId


@dataclass(frozen=True, slots=True)
class DvcNamespace:
    """Canonical mapping between a project and its S3-compatible DVC namespace."""

    remote_base_url: str
    bucket: str
    base_prefix: str

    @classmethod
    def parse(cls, remote_base_url: str) -> DvcNamespace:
        normalized = remote_base_url.rstrip("/")
        parsed = urlsplit(normalized)
        if parsed.scheme != "s3" or not parsed.netloc:
            raise ValueError("DVC remote base URL must use s3:// and include a bucket")
        if parsed.query or parsed.fragment or parsed.username or parsed.password or parsed.port:
            raise ValueError("DVC remote base URL must contain only a bucket and path")
        prefix = parsed.path.strip("/")
        if any(part in {".", ".."} for part in prefix.split("/")):
            raise ValueError("DVC remote base URL contains an invalid namespace prefix")
        return cls(normalized, parsed.netloc, prefix)

    def project_prefix(self, project_id: PublicId) -> str:
        return f"{self.base_prefix}/{project_id}" if self.base_prefix else str(project_id)

    def project_remote_url(self, project_id: PublicId) -> str:
        return f"s3://{self.bucket}/{self.project_prefix(project_id)}"
