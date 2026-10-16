"""Original-action recovery is conservative about absent evidence and restored DBs."""

from pathlib import Path
import sqlite3


from scoperelay.storage.database import open_existing
from scoperelay.storage.writer_owner import ApplicationWriter, WriterBusy
from scoperelay.trusted.resource import (
    ProtectedCommunicationResource,
    ResourceCallerContext,
)
import scoperelay.trusted.resource as resource_module
from tests.security.test_protected_resource import EPOCH, _admit, routine_db  # noqa: F401


def _identity(path: Path):
    with ApplicationWriter.start(path, timeout=5.0) as owner:
        return ProtectedCommunicationResource(path, owner).capture_original_identity(
            "action-01", "material-01", ResourceCallerContext("ep-01", EPOCH)
        )


def _reconcile(path: Path, original):
    try:
        with ApplicationWriter.start(path) as owner:
            return ProtectedCommunicationResource(path, owner).reconcile_action(
                original
            )
    except (FileNotFoundError, WriterBusy):
        return ProtectedCommunicationResource(path).reconcile_action(original)


def test_lost_ack_reconciles_original_commit(routine_db: Path, monkeypatch):
    original = _identity(routine_db)

    def fail_after_commit(stage: str):
        if stage == "after_commit":
            raise RuntimeError("ack lost")

    monkeypatch.setattr(resource_module, "_checkpoint", fail_after_commit)
    uncertain = _admit(routine_db)
    assert uncertain.outcome == "INDETERMINATE"
    recovered = _reconcile(routine_db, original)
    assert recovered.outcome == "RECONCILED_COMMITTED"
    assert recovered.effect_id and recovered.attempt_id and recovered.journal_event_seq
    con = open_existing(routine_db)
    assert con.execute("SELECT count(*) FROM queue_effects").fetchone() == (1,)
    con.close()


def test_registered_quiescent_original_is_terminally_fenced(routine_db: Path):
    original = _identity(routine_db)
    outcome = _reconcile(routine_db, original)
    assert outcome.outcome == "RECONCILED_NOT_COMMITTED"
    assert outcome.reason_code == "TERMINAL_FENCE_COMMITTED"
    con = open_existing(routine_db)
    assert con.execute(
        "SELECT state FROM original_actions WHERE action_id='action-01'"
    ).fetchone() == ("CLOSED_NOT_COMMITTED",)
    assert con.execute("SELECT count(*) FROM reconciliation_events").fetchone() == (1,)
    con.close()
    assert _admit(routine_db).outcome == "EXPLICIT_POLICY_DENIAL"


def test_untracked_original_remains_indeterminate(routine_db: Path):
    original = _identity(routine_db)
    con = open_existing(routine_db)
    # Test-only corruption/legacy simulation cannot silently establish lifecycle evidence.
    con.execute("DROP TRIGGER original_actions_no_delete")
    con.execute("DELETE FROM original_actions WHERE action_id='action-01'")
    con.close()
    outcome = _reconcile(routine_db, original)
    assert outcome.outcome == "INDETERMINATE"
    assert outcome.reason_code == "ORIGINAL_WRITER_LIFECYCLE_UNTRACKED"


def test_consistent_backup_cannot_prove_original_absence(
    routine_db: Path, tmp_path: Path
):
    original = _identity(routine_db)
    backup = tmp_path / "backup.db"
    source = open_existing(routine_db)
    target = sqlite3.connect(backup)
    source.backup(target)
    target.close()
    source.close()
    verified_backup = sqlite3.connect(backup)
    assert verified_backup.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    assert verified_backup.execute("PRAGMA foreign_key_check").fetchone() is None
    verified_backup.close()
    _admit(routine_db)
    restored = _reconcile(backup, original)
    assert restored.outcome == "INDETERMINATE"
    assert restored.reason_code == "ORIGINAL_DATABASE_FILE_CHANGED"
    live = _reconcile(routine_db, original)
    assert live.outcome == "RECONCILED_COMMITTED"


def test_missing_original_does_not_create_empty_db(routine_db: Path, tmp_path: Path):
    original = _identity(routine_db)
    missing = tmp_path / "missing.db"
    result = _reconcile(missing, original)
    assert result.outcome == "INDETERMINATE" and not missing.exists()


def test_corrupt_journal_is_not_reconciled_committed(routine_db: Path):
    original = _identity(routine_db)
    _admit(routine_db)
    con = open_existing(routine_db)
    con.execute(
        "UPDATE journal_events SET event_type='CORRUPTED' WHERE action_id='action-01'"
    )
    con.close()
    result = _reconcile(routine_db, original)
    assert result.outcome == "INDETERMINATE"
    assert result.reason_code == "INCONSISTENT_ACCEPTED_EVIDENCE"
