from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest
from homebrew_mlflow.application import AttachmentService, ResourceConflict, UploadAttachment
from homebrew_mlflow.domain import (
    ProjectRole,
    PublicId,
    ResourceKind,
    Run,
    RunAttachment,
)

NOW = datetime(2026, 8, 17, 12, tzinfo=UTC)


@dataclass
class UnitOfWork:
    stored_run: Run
    actor_id: PublicId
    stored: dict[str, RunAttachment] = field(default_factory=dict)
    commits: int = 0

    def run(self, run_id: PublicId) -> Run | None:
        return self.stored_run if self.stored_run.id == run_id else None

    def project_role(self, project_id: PublicId, principal_id: PublicId) -> ProjectRole | None:
        if project_id == self.stored_run.project_id and principal_id == self.actor_id:
            return ProjectRole.CONTRIBUTOR
        return None

    def attachment(self, _run_id: PublicId, path: str) -> RunAttachment | None:
        return self.stored.get(path)

    def list_attachments(self, _run_id: PublicId) -> tuple[RunAttachment, ...]:
        return tuple(self.stored.values())

    def attachment_totals(self, _run_id: PublicId) -> tuple[int, int]:
        return len(self.stored), sum(item.size for item in self.stored.values())

    def add_attachment(self, attachment: RunAttachment) -> None:
        self.stored[attachment.path] = attachment

    def commit(self) -> None:
        self.commits += 1


@dataclass
class Objects:
    values: dict[str, bytes] = field(default_factory=dict)

    def put(self, object_key: str, content: bytes, _media_type: str) -> None:
        self.values[object_key] = content

    def get(self, object_key: str) -> bytes:
        return self.values[object_key]


def fixture() -> tuple[PublicId, Run, UnitOfWork, Objects, AttachmentService]:
    actor = PublicId.generate(ResourceKind.PRINCIPAL)
    run = Run.create(
        PublicId.generate(ResourceKind.PROJECT),
        PublicId.generate(ResourceKind.EXPERIMENT),
        PublicId.generate(ResourceKind.REPOSITORY),
        actor,
        ("python", "train.py"),
        NOW,
    ).start(NOW)
    uow = UnitOfWork(run, actor)
    objects = Objects()
    return actor, run, uow, objects, AttachmentService(uow, objects)


def test_attachment_upload_is_path_safe_immutable_and_idempotent() -> None:
    actor, run, uow, objects, service = fixture()
    command = UploadAttachment(
        run.id, run.project_id, "plots/loss.svg", b"<svg />", "image/svg+xml", NOW
    )

    first = service.upload(actor, command)
    replay = service.upload(actor, command)
    downloaded, content = service.download(actor, run.id, run.project_id, "plots/loss.svg")

    assert replay == first == downloaded
    assert content == b"<svg />"
    assert len(objects.values) == 1
    assert uow.commits == 1

    with pytest.raises(ResourceConflict, match="immutable"):
        service.upload(
            actor,
            UploadAttachment(
                run.id,
                run.project_id,
                "plots/loss.svg",
                b"different",
                "image/svg+xml",
                NOW,
            ),
        )


@pytest.mark.parametrize(
    ("path", "media_type"),
    [("../secret", "text/plain"), ("model.pkl", "application/octet-stream")],
)
def test_attachment_rejects_unsafe_paths_and_binary_model_bundles(
    path: str, media_type: str
) -> None:
    actor, run, _uow, _objects, service = fixture()
    with pytest.raises(ValueError):
        service.upload(
            actor,
            UploadAttachment(run.id, run.project_id, path, b"content", media_type, NOW),
        )
