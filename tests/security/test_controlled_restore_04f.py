"""Controlled restore preserves history and never activates staged old state."""

from pathlib import Path
import sqlite3
import shutil
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from scoperelay.storage.database import open_existing
from scoperelay.storage.restore import RestoreError, stage_restore
from scoperelay.storage.schema import initialize
from scoperelay.storage.writer_owner import (
    ApplicationWriter,
    WriterBusy,
    quarantine_path,
)
from scoperelay.trusted.resource import (
    ProtectedCommunicationResource,
    ResourceCallerContext,
)
import scoperelay.trusted.resource as resource_module
from tests.security.test_protected_resource import EPOCH, NOW, STAMP, routine_db  # noqa: F401
from tests.security.test_recovery_04c import _identity


def _backup(source_path: Path, destination: Path) -> None:
    source = open_existing(source_path)
    target = sqlite3.connect(destination)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()


def _admit(path: Path, owner: ApplicationWriter):
    return ProtectedCommunicationResource(path, owner).admit_material_action(
        "action-01", "material-01", None, ResourceCallerContext("ep-01", EPOCH), at=NOW
    )


def test_old_backup_is_staged_not_activated_and_original_commit_survives(
    routine_db, tmp_path
):
    original = _identity(routine_db)
    older = tmp_path / "older.db"
    _backup(routine_db, older)
    with ApplicationWriter.start(routine_db) as owner:
        accepted = _admit(routine_db, owner)
        assert accepted.outcome == "ACCEPTED_COMMITTED"
        assessment = stage_restore(owner, older, tmp_path / "review")
        assert assessment.status == "QUARANTINED"
        assert "MISSING_OR_CHANGED_QUEUE_EFFECTS" in assessment.findings
        assert "MISSING_OR_CHANGED_JOURNAL_EVENTS" in assessment.findings
        assert _admit(routine_db, owner).outcome == "INDETERMINATE"
        assert (
            ProtectedCommunicationResource(routine_db, owner)
            .reconcile_action(original)
            .outcome
            == "RECONCILED_COMMITTED"
        )
    with ApplicationWriter.start(routine_db) as reopened:
        assert _admit(routine_db, reopened).outcome == "INDETERMINATE"
    with ApplicationWriter.start(assessment.staged_database) as staged_owner:
        assert (
            _admit(assessment.staged_database, staged_owner).outcome == "INDETERMINATE"
        )
    archived = open_existing(assessment.original_archive)
    staged = open_existing(assessment.staged_database)
    try:
        assert archived.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert archived.execute("PRAGMA foreign_key_check").fetchone() is None
        assert archived.execute("SELECT count(*) FROM queue_effects").fetchone() == (1,)
        assert staged.execute("SELECT count(*) FROM queue_effects").fetchone() == (0,)
        assert routine_db.exists() and quarantine_path(routine_db).exists()
    finally:
        staged.close()
        archived.close()


def test_matching_backup_still_needs_explicit_decision(routine_db, tmp_path):
    with ApplicationWriter.start(routine_db) as owner:
        assert _admit(routine_db, owner).outcome == "ACCEPTED_COMMITTED"
        matching = tmp_path / "matching.db"
        _backup(routine_db, matching)
        assessment = stage_restore(owner, matching, tmp_path / "matching-review")
        assert assessment.status == "QUARANTINED" and not assessment.findings
        assert _admit(routine_db, owner).outcome == "INDETERMINATE"


def test_invalid_backup_leaves_original_held(routine_db, tmp_path):
    invalid = tmp_path / "invalid.db"
    invalid.write_bytes(b"not sqlite")
    with ApplicationWriter.start(routine_db) as owner:
        with pytest.raises((sqlite3.DatabaseError, RestoreError)):
            stage_restore(owner, invalid, tmp_path / "invalid-review")
        assert quarantine_path(routine_db).exists()
        assert _admit(routine_db, owner).outcome == "INDETERMINATE"


def test_wrong_database_identity_is_reported_and_never_activated(routine_db, tmp_path):
    backup = tmp_path / "foreign.db"
    initialize(backup)
    with ApplicationWriter.start(routine_db) as owner:
        assessment = stage_restore(owner, backup, tmp_path / "foreign-review")
        assert "DATABASE_IDENTITY_MISMATCH" in assessment.findings
        assert _admit(routine_db, owner).outcome == "INDETERMINATE"


def test_missing_continuity_on_restart_quarantines_admission(routine_db):
    _identity(routine_db)
    witness = routine_db.with_name(routine_db.name + ".continuity")
    saved = witness.read_bytes()
    witness.unlink()  # Disposable synthetic DB only.
    with ApplicationWriter.start(routine_db) as owner:
        assert _admit(routine_db, owner).outcome == "INDETERMINATE"
        assert quarantine_path(routine_db).exists()
    witness.write_bytes(saved)  # Restore visible witness, but not trusted clearance.
    with ApplicationWriter.start(routine_db) as owner:
        assert _admit(routine_db, owner).outcome == "INDETERMINATE"


def test_witness_only_old_restore_quarantines_known_commit(routine_db, tmp_path):
    _identity(routine_db)
    witness = routine_db.with_name(routine_db.name + ".continuity")
    old = tmp_path / "old.continuity"
    shutil.copyfile(witness, old)
    with ApplicationWriter.start(routine_db) as owner:
        assert _admit(routine_db, owner).outcome == "ACCEPTED_COMMITTED"
    shutil.copyfile(old, witness)  # Unsupported manual rollback on disposable DB.
    with ApplicationWriter.start(routine_db) as owner:
        assert _admit(routine_db, owner).outcome == "INDETERMINATE"


def test_corrupted_witness_quarantines_protected_admission(routine_db):
    _identity(routine_db)
    witness = routine_db.with_name(routine_db.name + ".continuity")
    witness.write_bytes(b"{broken")  # Disposable synthetic sidecar only.
    with ApplicationWriter.start(routine_db) as owner:
        assert _admit(routine_db, owner).outcome == "INDETERMINATE"


def test_restore_cannot_overwrite_original_or_use_it_as_backup(routine_db, tmp_path):
    with ApplicationWriter.start(routine_db) as owner:
        with pytest.raises(RestoreError):
            stage_restore(owner, routine_db, tmp_path / "review")
        backup = tmp_path / "backup.db"
        _backup(routine_db, backup)
        with pytest.raises(RestoreError):
            stage_restore(owner, backup, routine_db)
        assert not quarantine_path(routine_db).exists()


def test_restore_refuses_inflight_original_and_keeps_owner(
    routine_db, tmp_path, monkeypatch
):
    backup = tmp_path / "backup.db"
    _backup(routine_db, backup)
    entered, release = Event(), Event()

    def pause(stage):
        if stage == "after_queue":
            entered.set()
            assert release.wait(timeout=10)

    monkeypatch.setattr(resource_module, "_checkpoint", pause)
    with ApplicationWriter.start(routine_db) as owner:
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(_admit, routine_db, owner)
            assert entered.wait(timeout=10)
            with pytest.raises(WriterBusy):
                stage_restore(owner, backup, tmp_path / "review")
            assert not quarantine_path(routine_db).exists()
            release.set()
            assert pending.result(timeout=10).outcome == "ACCEPTED_COMMITTED"


def test_stale_wal_reader_cannot_authorize_after_grant_revocation(routine_db):
    reader = open_existing(routine_db)
    reader.execute("BEGIN")
    assert reader.execute(
        "SELECT version FROM manager_grants WHERE grant_id='grant-01'"
    ).fetchone() == (1,)
    writer = open_existing(routine_db)
    writer.execute("BEGIN IMMEDIATE")
    writer.execute(
        "UPDATE manager_grants SET revoked_at=?,version=2 WHERE grant_id='grant-01'",
        (STAMP,),
    )
    writer.execute("COMMIT")
    writer.close()
    assert reader.execute(
        "SELECT version FROM manager_grants WHERE grant_id='grant-01'"
    ).fetchone() == (1,)
    with ApplicationWriter.start(routine_db) as owner:
        result = _admit(routine_db, owner)
    assert (result.outcome, result.reason_code) == (
        "EXPLICIT_POLICY_DENIAL",
        "GRANT_REVOKED_OR_EXPIRED",
    )
    assert reader.execute(
        "SELECT version FROM manager_grants WHERE grant_id='grant-01'"
    ).fetchone() == (1,)
    reader.execute("ROLLBACK")
    reader.close()
    con = open_existing(routine_db)
    assert con.execute("SELECT count(*) FROM queue_effects").fetchone() == (0,)
    con.close()
