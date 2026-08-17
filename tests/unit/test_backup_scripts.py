from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_backup_contains_every_owned_control_plane_secret_and_store() -> None:
    script = (ROOT / "deploy/backup/backup.sh").read_text(encoding="utf-8")

    assert "platform.pgdump" in script
    assert "infisical.pgdump" in script
    assert "gitlab-config.tar.gz" in script
    assert "platform-secrets.tar.gz" in script
    assert "objects/research" in script
    assert "objects/homebrew-mlflow" in script
    assert "latest-complete" in script


def test_restore_is_guarded_quiesces_writers_and_checks_gitlab() -> None:
    script = (ROOT / "deploy/backup/restore.sh").read_text(encoding="utf-8")

    assert '--confirm-empty-target' in script
    assert 'stop gateway api publication-worker-1' in script
    assert "gitlab-ctl stop puma" in script
    assert "gitlab-ctl stop sidekiq" in script
    assert "platform-secrets.tar.gz" in script
    assert "chown git:git" in script
    assert "gitlab-rake gitlab:check SANITIZE=true" in script
