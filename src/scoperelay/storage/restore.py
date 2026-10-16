"""Local operator-only restore *staging*, never an in-place activation path.

All files created here are diagnostic copies in the same trust domain. They
cannot establish history that has already been coherently erased everywhere.
"""

from dataclasses import dataclass
from pathlib import Path
import json
import shutil
import sqlite3

from .database import check_integrity, open_existing
from .schema import _verify
from .writer_owner import ApplicationWriter, OwnerInvalid, quarantine_path


class RestoreError(RuntimeError):
    pass


@dataclass(frozen=True)
class RestoreAssessment:
    status: str
    findings: tuple[str, ...]
    original_archive: Path
    staged_database: Path


def _snapshot(source: sqlite3.Connection, path: Path) -> None:
    target = sqlite3.connect(path, autocommit=True)
    try:
        source.backup(target)
    finally:
        target.close()


def _rows(con: sqlite3.Connection, table: str) -> set[tuple[object, ...]]:
    # Only fixed, internal table identifiers reach this helper.
    return {tuple(row) for row in con.execute(f"SELECT * FROM {table}")}


def stage_restore(
    owner: ApplicationWriter, backup_path: Path, staging_dir: Path
) -> RestoreAssessment:
    """Quarantine admission, preserve original, and inspect a backup in NEW space.

    No activation or unquarantine operation is provided. The caller must be a
    trusted local operator workflow, never a model/browser-facing dispatcher.
    """
    if type(owner) is not ApplicationWriter:
        raise OwnerInvalid("active application writer required")
    owner._verify()
    backup = backup_path.resolve(strict=True)
    if not backup.is_file() or backup == owner.path:
        raise RestoreError("backup must be a separate existing regular file")
    destination = staging_dir.resolve(strict=False)
    if destination.exists() or not destination.parent.is_dir():
        raise RestoreError("staging directory must be new under an existing parent")
    if destination == owner.path or owner.path in destination.parents:
        raise RestoreError("staging must not overlap original database")

    # The one-way hold is established before any backup/read/restore work.
    owner.quarantine("CONTROLLED_RESTORE_REVIEW_REQUIRED")
    destination.mkdir()
    archive = destination / "original-snapshot.db"
    staged = destination / "candidate.db"
    findings: list[str] = []
    with owner.operation(recovery=True):
        original = open_existing(owner.path)
        try:
            check_integrity(original)
            _verify(original)
            _snapshot(original, archive)
            original_uuid = original.execute(
                "SELECT identity_uuid FROM database_identity WHERE singleton=1"
            ).fetchone()[0]
            original_epochs = {
                row[0]
                for row in original.execute(
                    "SELECT DISTINCT fixture_epoch FROM episodes"
                )
            }
            for suffix in (".continuity", "-wal", "-shm"):
                source = owner.path.with_name(owner.path.name + suffix)
                if source.is_file():
                    shutil.copyfile(source, destination / ("original" + suffix))
            # Preserve the original quarantine reason as diagnostic evidence.
            shutil.copyfile(
                quarantine_path(owner.path), destination / "original.quarantine"
            )

            candidate_source = open_existing(backup)
            try:
                check_integrity(candidate_source)
                _verify(candidate_source)
                _snapshot(candidate_source, staged)
            finally:
                candidate_source.close()
            candidate = open_existing(staged)
            try:
                check_integrity(candidate)
                _verify(candidate)
                candidate_uuid = candidate.execute(
                    "SELECT identity_uuid FROM database_identity WHERE singleton=1"
                ).fetchone()[0]
                candidate_epochs = {
                    row[0]
                    for row in candidate.execute(
                        "SELECT DISTINCT fixture_epoch FROM episodes"
                    )
                }
                if candidate_uuid != original_uuid:
                    findings.append("DATABASE_IDENTITY_MISMATCH")
                if candidate_epochs != original_epochs:
                    findings.append("FIXTURE_EPOCH_MISMATCH")
                for table in (
                    "original_actions",
                    "reconciliation_events",
                    "queue_effects",
                    "resource_attempts",
                    "journal_events",
                    "escalation_approvals",
                ):
                    if not _rows(original, table) <= _rows(candidate, table):
                        findings.append("MISSING_OR_CHANGED_" + table.upper())
            finally:
                candidate.close()
        finally:
            original.close()
    # A manifest is descriptive, not an independent history anchor. A staged
    # DB must remain non-authoritative even if its rows happen to match.
    (destination / "assessment.json").write_text(
        json.dumps(
            {
                "status": "QUARANTINED",
                "findings": findings,
                "activation": "NOT_IMPLEMENTED_REQUIRES_TRUSTED_ARCHITECTURE_DECISION",
            },
            sort_keys=True,
            indent=2,
        ),
        encoding="utf-8",
    )
    quarantine_path(staged).write_text("STAGED_NON_AUTHORITATIVE\n", encoding="utf-8")
    return RestoreAssessment("QUARANTINED", tuple(findings), archive, staged)
