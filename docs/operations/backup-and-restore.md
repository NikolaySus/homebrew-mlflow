# Backup and restore runbook

Backup automation is opt-in and disabled by default: this repository does not install a timer or
cron job. When an operator enables backups, the production policy is a daily off-host S3 backup
(RPO 24 hours), an eight-hour recovery target, and a production-like restore drill every quarter.
A backup is not considered restorable until `latest-complete` is written after PostgreSQL, GitLab,
Infisical, and both owned object buckets finish.

Run `deploy/backup/backup.sh` daily from a locked-down host timer with the `BACKUP_S3_*` variables
provided by the operational secret manager. The account must be limited to the configured backup
bucket. Keep GitLab configuration and `gitlab-secrets.json` under the same encrypted off-host policy;
without the latter, encrypted GitLab data cannot be recovered.

Include the Compose `platform-secrets` volume in the encrypted provider backup set. It contains the
GitLab OAuth coordinates, GitLab integration token, and Infisical reconciliation credential that bind
the platform database to the restored provider databases. Never print these files during backup or
restore validation; verify only presence, size, permissions, and an authenticated provider request.
The production deployment configuration must also preserve Infisical's encryption key separately;
restoring its PostgreSQL data without the same key does not recover decryptable secrets.

The automatically created GitLab integration token expires after 90 days. Rotate it during a planned
maintenance window before expiry: create the replacement in GitLab, atomically replace only
`gitlab-integration-token` in `platform-secrets`, recreate API and worker containers, verify an
authenticated `/api/v4/user` request, and then revoke the previous token. OAuth and Infisical identity
credential rotation follows the same create-switch-verify-revoke order. Record rotations in the
operations audit log and refresh the encrypted backup immediately afterward.

Quarterly drill:

1. Provision an isolated target with empty named volumes and no production ingress.
2. Select a completed timestamp, set `RESTORE_ID`, and run
   `deploy/backup/restore.sh --confirm-empty-target`.
3. Upgrade schema to the recorded application version and start the stack.
4. Verify login, Git clone, one canonical Run, MLflow metrics, a published file and directory,
   shared-version access, and byte-for-byte DVC materialization.
5. Record elapsed recovery time, missing objects, backup age, and corrective work. The drill fails
   if recovery exceeds eight hours or any referenced byte cannot be reconstructed.

The restore script is intentionally destructive to its target databases. It refuses to run without
the explicit empty-target confirmation and must never be aimed at a running production deployment.
