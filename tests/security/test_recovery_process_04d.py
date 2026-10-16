"""Windows child-process ownership and real process death at API-108 phases."""

from pathlib import Path
from dataclasses import replace
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Event

from scoperelay.storage.database import check_integrity, open_existing
from scoperelay.storage.fixture_loader import EPOCH
from scoperelay.storage.schema import initialize
from scoperelay.storage.writer_owner import ApplicationWriter
from scoperelay.trusted.resource import ProtectedCommunicationResource
from scoperelay.trusted.resource import ResourceEvidenceError
import pytest
import scoperelay.trusted.resource as resource_module
from tests.security.test_protected_resource import (
    _admit,
    _second_same_business,
    routine_db,  # noqa: F401 - imported so pytest registers the shared fixture.
)
from tests.security.test_recovery_04c import _identity, _reconcile


def _load(path: Path, action: str = "action-01", epoch: str = EPOCH):
    with ApplicationWriter.start(path) as owner:
        return ProtectedCommunicationResource(path, owner).load_original_identity(
            action, epoch
        )


WORKER = Path(__file__).parent / "recovery_worker.py"


def _run(mode: str, path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(WORKER), mode, str(path)],
        capture_output=True,
        text=True,
        timeout=15,
    )


def test_real_process_owner_excludes_competitor_and_survives_stale_lockfile(
    routine_db: Path,
):
    original = _identity(routine_db)
    child = subprocess.Popen(
        [sys.executable, str(WORKER), "hold_owner", str(routine_db)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "OWNED"
        competing = _run("try_owner", routine_db)
        assert competing.returncode == 0 and competing.stdout.strip() == "BUSY"
        uncertain = _reconcile(routine_db, original)
        assert uncertain.outcome == "INDETERMINATE"
        assert uncertain.reason_code == "ORIGINAL_WRITER_OWNERSHIP_UNAVAILABLE"
        child.kill()
        assert child.wait(timeout=10) != 0
        assert routine_db.with_name(routine_db.name + ".owner").exists()
        reopened = _run("try_owner", routine_db)
        assert reopened.returncode == 0 and reopened.stdout.strip() == "OWNED"
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=10)
        for stream in (child.stdin, child.stdout, child.stderr):
            if stream is not None:
                stream.close()


def test_real_crash_before_begin_then_terminal_fence(routine_db: Path):
    original = _identity(routine_db)
    crashed = _run("crash_before_begin", routine_db)
    assert crashed.returncode == 77 and "REACHED:before_begin" in crashed.stdout
    con = open_existing(routine_db)
    assert con.execute("SELECT count(*) FROM queue_effects").fetchone() == (0,)
    con.close()
    restarted_identity = _load(routine_db)
    assert restarted_identity == original
    recovered = _reconcile(routine_db, restarted_identity)
    assert recovered.outcome == "RECONCILED_NOT_COMMITTED"
    assert _admit(routine_db).outcome == "EXPLICIT_POLICY_DENIAL"


def test_active_admission_then_reconciliation_sees_commit(
    routine_db: Path, monkeypatch
):
    original = _identity(routine_db)
    entered = Event()
    release = Event()

    def pause(stage: str):
        if stage == "after_queue":
            entered.set()
            assert release.wait(timeout=10)

    monkeypatch.setattr(resource_module, "_checkpoint", pause)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(_admit, routine_db)
        assert entered.wait(timeout=10)
        while_active = _reconcile(routine_db, original)
        assert while_active.outcome == "INDETERMINATE"
        release.set()
        assert pending.result(timeout=10).outcome == "ACCEPTED_COMMITTED"
    final = _reconcile(routine_db, original)
    assert final.outcome == "RECONCILED_COMMITTED"


def test_fence_first_prevents_delayed_original_and_double_recovery(routine_db: Path):
    original = _identity(routine_db)
    with ApplicationWriter.start(routine_db) as owner:
        resource = ProtectedCommunicationResource(routine_db, owner)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(resource.reconcile_action, (original, original)))
    assert {r.outcome for r in results} <= {"RECONCILED_NOT_COMMITTED", "INDETERMINATE"}
    assert any(r.outcome == "RECONCILED_NOT_COMMITTED" for r in results)
    assert _reconcile(routine_db, original).outcome == "RECONCILED_NOT_COMMITTED"
    assert _admit(routine_db).outcome == "EXPLICIT_POLICY_DENIAL"
    con = open_existing(routine_db)
    assert con.execute("SELECT count(*) FROM queue_effects").fetchone() == (0,)
    assert con.execute("SELECT count(*) FROM reconciliation_events").fetchone() == (1,)
    con.close()


def test_terminal_fence_blocks_legacy_direct_insert_and_new_id_same_business(
    routine_db: Path,
):
    original = _identity(routine_db)
    assert _reconcile(routine_db, original).outcome == ("RECONCILED_NOT_COMMITTED")
    con = open_existing(routine_db)
    material = con.execute(
        "SELECT action_id,business_key,recipient_contact_id,material_sha256,rendered_body "
        "FROM material_actions WHERE material_action_id='material-01'"
    ).fetchone()
    with pytest.raises(
        sqlite3.IntegrityError,
        match="original action not dispatched or business fenced",
    ):
        con.execute(
            "INSERT INTO queue_effects VALUES (?,?,?,?,?,?,?,?)",
            (
                "effect-legacy",
                material[0],
                material[1],
                "material-01",
                material[2],
                material[3],
                material[4],
                "2026-10-14T00:00:00.000000Z",
            ),
        )
    con.close()
    _second_same_business(routine_db)
    attempted = _admit(routine_db, action="action-02", material="material-02")
    assert attempted.outcome == "EXPLICIT_POLICY_DENIAL"
    assert attempted.reason_code == "BUSINESS_IDENTITY_TERMINALLY_FENCED"
    con = open_existing(routine_db)
    assert con.execute("SELECT count(*) FROM queue_effects").fetchone() == (0,)
    con.close()


def test_lost_fence_ack_stays_unknown_until_durable_witness_rechecked(
    routine_db: Path, monkeypatch
):
    original = _identity(routine_db)

    def lose_ack(stage: str):
        if stage == "after_terminal_fence_commit":
            raise RuntimeError("fence ack lost")

    monkeypatch.setattr(resource_module, "_checkpoint", lose_ack)
    first = _reconcile(routine_db, original)
    assert first.outcome == "INDETERMINATE"
    assert first.reason_code == "TERMINAL_FENCE_ACK_UNCERTAIN"
    monkeypatch.setattr(resource_module, "_checkpoint", lambda stage: None)
    second = _reconcile(routine_db, original)
    assert second.outcome == "RECONCILED_NOT_COMMITTED"
    assert _admit(routine_db).outcome == "EXPLICIT_POLICY_DENIAL"


def test_real_crash_during_uncommitted_queue_rolls_back_and_fences(routine_db: Path):
    crashed = _run("crash_after_queue", routine_db)
    assert crashed.returncode == 77 and "REACHED:after_queue" in crashed.stdout
    con = open_existing(routine_db)
    assert con.execute("SELECT count(*) FROM queue_effects").fetchone() == (0,)
    assert con.execute("SELECT count(*) FROM resource_attempts").fetchone() == (0,)
    assert con.execute("SELECT count(*) FROM journal_events").fetchone() == (0,)
    check_integrity(con)
    con.close()
    recovered = _reconcile(routine_db, _identity(routine_db))
    assert recovered.outcome == "RECONCILED_NOT_COMMITTED"
    assert _admit(routine_db).outcome == "EXPLICIT_POLICY_DENIAL"


def test_real_crash_after_commit_reconciles_original_evidence(routine_db: Path):
    crashed = _run("crash_after_commit", routine_db)
    assert crashed.returncode == 77 and "REACHED:after_commit" in crashed.stdout
    recovered = _reconcile(routine_db, _identity(routine_db))
    assert recovered.outcome == "RECONCILED_COMMITTED"
    assert recovered.effect_id and recovered.attempt_id and recovered.journal_event_seq
    con = open_existing(routine_db)
    assert con.execute("SELECT count(*) FROM queue_effects").fetchone() == (1,)
    check_integrity(con)
    con.close()


def test_in_place_old_snapshot_cannot_prove_noncommit(routine_db: Path, tmp_path: Path):
    original = _identity(routine_db)
    old_snapshot = tmp_path / "old-snapshot.db"
    source = open_existing(routine_db)
    target = sqlite3.connect(old_snapshot)
    source.backup(target)
    target.close()
    source.close()
    assert _admit(routine_db).outcome == "ACCEPTED_COMMITTED"
    before = routine_db.stat().st_ino
    snapshot = sqlite3.connect(old_snapshot)
    destination = open_existing(routine_db)
    snapshot.backup(destination)
    destination.close()
    snapshot.close()
    assert routine_db.stat().st_ino == before
    result = _reconcile(routine_db, original)
    assert result.outcome == "INDETERMINATE"
    assert result.reason_code == "ORIGINAL_HISTORY_CONTINUITY_UNPROVED"
    retry = _admit(routine_db)
    assert retry.outcome == "INDETERMINATE"
    con = open_existing(routine_db)
    assert con.execute("SELECT count(*) FROM queue_effects").fetchone() == (0,)
    con.close()


def test_wrong_epoch_and_deleted_continuity_never_prove_absence(routine_db: Path):
    original = _identity(routine_db)
    wrong_epoch = _reconcile(routine_db, replace(original, fixture_epoch="wrong-epoch"))
    assert wrong_epoch.outcome == "INDETERMINATE"
    with pytest.raises(ResourceEvidenceError):
        _load(routine_db, epoch="wrong-epoch")
    continuity = routine_db.with_name(routine_db.name + ".continuity")
    continuity.unlink()  # Disposable test DB only: simulate provenance loss.
    result = _reconcile(routine_db, original)
    assert result.outcome == "INDETERMINATE"
    assert result.reason_code == "ORIGINAL_HISTORY_CONTINUITY_UNPROVED"


def test_malformed_continuity_never_proves_absence(routine_db: Path):
    original = _identity(routine_db)
    continuity = routine_db.with_name(routine_db.name + ".continuity")
    continuity.write_bytes(b"[]\n")  # Disposable test sidecar only.
    outcome = _reconcile(routine_db, original)
    assert outcome.outcome == "INDETERMINATE"
    assert outcome.reason_code == "ORIGINAL_HISTORY_CONTINUITY_UNPROVED"


def test_online_backup_of_committed_original_has_consistent_evidence(
    routine_db: Path, tmp_path: Path
):
    original = _identity(routine_db)
    accepted = _admit(routine_db)
    assert accepted.outcome == "ACCEPTED_COMMITTED"
    backup = tmp_path / "committed-backup.db"
    source = open_existing(routine_db)
    destination = sqlite3.connect(backup)
    source.backup(destination)
    destination.close()
    source.close()
    read = sqlite3.connect(backup)
    assert read.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    assert read.execute("PRAGMA foreign_key_check").fetchone() is None
    assert read.execute("SELECT count(*) FROM queue_effects").fetchone() == (1,)
    assert read.execute(
        "SELECT count(*) FROM journal_events WHERE event_type='ACCEPTED_COMMITTED'"
    ).fetchone() == (1,)
    assert read.execute("SELECT identity_uuid FROM database_identity").fetchone() == (
        original.database_uuid,
    )
    read.close()


def test_forward_upgrade_from_v2_preserves_legacy_accepted_effect(routine_db: Path):
    accepted = _admit(routine_db)
    assert accepted.outcome == "ACCEPTED_COMMITTED"
    con = open_existing(routine_db)
    # Reconstruct the exact pre-003 schema shape on this disposable DB only.
    con.execute("DROP TABLE candidate_quote_bindings")
    con.execute("DROP TABLE episode_candidate_protocols")
    con.execute("DELETE FROM schema_migrations WHERE version=4")
    con.execute("DROP TRIGGER queue_effects_requires_dispatch")
    con.execute("DROP TABLE reconciliation_events")
    con.execute("DROP TABLE original_actions")
    con.execute("DELETE FROM schema_migrations WHERE version=3")
    con.execute("PRAGMA user_version=2")
    con.close()
    initialize(routine_db)
    check = open_existing(routine_db)
    assert check.execute("PRAGMA user_version").fetchone() == (4,)
    assert check.execute("SELECT effect_id FROM queue_effects").fetchone() == (
        accepted.effect_id,
    )
    check_integrity(check)
    check.close()
