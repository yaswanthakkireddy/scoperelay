"""Actual startup-held owner, Windows process lock and local shutdown barriers."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
from threading import Event

import pytest

from scoperelay.storage.database import open_existing
from scoperelay.storage.continuity import _path as continuity_path
from scoperelay.storage.writer_owner import ApplicationWriter, OwnerInvalid, WriterBusy
from scoperelay.trusted.resource import (
    ProtectedCommunicationResource,
    ResourceCallerContext,
)
import scoperelay.trusted.resource as resource_module
from tests.security.test_protected_resource import EPOCH, NOW, routine_db  # noqa: F401
from tests.security.test_recovery_04c import _identity


WORKER = Path(__file__).parent / "recovery_worker.py"


def _child(mode: str, path: Path):
    return subprocess.run(
        [sys.executable, str(WORKER), mode, str(path)],
        capture_output=True,
        text=True,
        timeout=15,
    )


def test_lifecycle_owner_persists_across_two_api108_calls_and_process_competitor(
    routine_db,
):
    with ApplicationWriter.start(routine_db) as owner:
        resource = ProtectedCommunicationResource(routine_db, owner)
        first = resource.admit_material_action(
            "action-01",
            "material-01",
            None,
            ResourceCallerContext("ep-01", EPOCH),
            at=NOW,
        )
        assert first.outcome == "ACCEPTED_COMMITTED"
        assert _child("try_lifecycle", routine_db).stdout.strip() == "BUSY"
        second = resource.admit_material_action(
            "action-01",
            "material-01",
            None,
            ResourceCallerContext("ep-01", EPOCH),
            at=NOW,
        )
        assert second.replayed and second.effect_id == first.effect_id
        assert _child("try_lifecycle", routine_db).stdout.strip() == "BUSY"
    assert _child("try_lifecycle", routine_db).stdout.strip() == "OWNED"


def test_real_process_death_releases_lifecycle_but_not_history(routine_db):
    child = subprocess.Popen(
        [sys.executable, str(WORKER), "hold_lifecycle", str(routine_db)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout.readline().strip() == "OWNED"
        assert _child("try_lifecycle", routine_db).stdout.strip() == "BUSY"
        child.kill()
        assert child.wait(timeout=10) != 0
        assert routine_db.with_name(routine_db.name + ".owner").exists()
        assert _child("try_lifecycle", routine_db).stdout.strip() == "OWNED"
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=10)
        for stream in (child.stdin, child.stdout, child.stderr):
            if stream is not None:
                stream.close()


def test_direct_api108_cannot_commit_without_startup_owner(routine_db):
    result = ProtectedCommunicationResource(routine_db).admit_material_action(
        "action-01", "material-01", None, ResourceCallerContext("ep-01", EPOCH), at=NOW
    )
    assert result.outcome == "TECHNICAL_ERROR" and result.attempt_id is None
    con = open_existing(routine_db)
    assert con.execute("SELECT count(*) FROM queue_effects").fetchone() == (0,)
    con.close()


def test_shutdown_stops_new_admission_and_waits_for_inflight(routine_db, monkeypatch):
    owner = ApplicationWriter.start(routine_db)
    resource = ProtectedCommunicationResource(routine_db, owner)
    entered, release = Event(), Event()

    def pause(stage):
        if stage == "after_queue":
            entered.set()
            assert release.wait(timeout=10)

    monkeypatch.setattr(resource_module, "_checkpoint", pause)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(
                resource.admit_material_action,
                "action-01",
                "material-01",
                None,
                ResourceCallerContext("ep-01", EPOCH),
                at=NOW,
            )
            assert entered.wait(timeout=10)
            with pytest.raises(WriterBusy):
                owner.close(timeout=0.01)
            blocked = resource.admit_material_action(
                "action-01",
                "material-01",
                None,
                ResourceCallerContext("ep-01", EPOCH),
                at=NOW,
            )
            assert blocked.outcome == "TECHNICAL_ERROR"
            assert _child("try_lifecycle", routine_db).stdout.strip() == "BUSY"
            release.set()
            accepted = pending.result(timeout=10)
            assert accepted.outcome == "ACCEPTED_COMMITTED"
        owner.close()
        assert _child("try_lifecycle", routine_db).stdout.strip() == "OWNED"
    finally:
        release.set()
        owner.close()


def test_recovery_excludes_local_inflight_original(routine_db, monkeypatch):
    original = _identity(routine_db)
    entered, release = Event(), Event()

    def pause(stage):
        if stage == "after_queue":
            entered.set()
            assert release.wait(timeout=10)

    monkeypatch.setattr(resource_module, "_checkpoint", pause)
    with ApplicationWriter.start(routine_db) as owner:
        resource = ProtectedCommunicationResource(routine_db, owner)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(
                resource.admit_material_action,
                "action-01",
                "material-01",
                None,
                ResourceCallerContext("ep-01", EPOCH),
                at=NOW,
            )
            assert entered.wait(timeout=10)
            assert resource.reconcile_action(original).outcome == "INDETERMINATE"
            release.set()
            assert pending.result(timeout=10).outcome == "ACCEPTED_COMMITTED"
        assert resource.reconcile_action(original).outcome == "RECONCILED_COMMITTED"


def test_owner_rejects_database_file_replacement(routine_db, tmp_path):
    with ApplicationWriter.start(routine_db) as owner:
        moved = tmp_path / "original-moved.db"
        routine_db.rename(moved)
        try:
            with pytest.raises(OwnerInvalid):
                with owner.operation():
                    pass
        finally:
            moved.rename(routine_db)


def test_closed_and_fabricated_owner_cannot_admit(routine_db):
    with pytest.raises(OwnerInvalid):
        ApplicationWriter(routine_db, object(), (0, 0, "forged"), object())
    fake = ProtectedCommunicationResource(routine_db, owner=True)
    result = fake.admit_material_action(
        "action-01", "material-01", None, ResourceCallerContext("ep-01", EPOCH), at=NOW
    )
    assert result.outcome == "TECHNICAL_ERROR"
    owner = ApplicationWriter.start(routine_db)
    resource = ProtectedCommunicationResource(routine_db, owner)
    with pytest.raises(OwnerInvalid):
        resource._admit_owned(
            "action-01",
            "material-01",
            None,
            ResourceCallerContext("ep-01", EPOCH),
            at=NOW,
        )
    owner.close()
    result = resource.admit_material_action(
        "action-01", "material-01", None, ResourceCallerContext("ep-01", EPOCH), at=NOW
    )
    assert result.outcome == "TECHNICAL_ERROR"
    con = open_existing(routine_db)
    assert con.execute("SELECT count(*) FROM queue_effects").fetchone() == (0,)
    con.close()


def test_real_crash_after_fsynced_intent_before_sqlite_commit_stays_unknown(routine_db):
    original = _identity(routine_db)
    crashed = _child("crash_after_commit_intent", routine_db)
    assert crashed.returncode == 77 and "REACHED:after_commit_intent" in crashed.stdout
    con = open_existing(routine_db)
    assert con.execute("SELECT count(*) FROM queue_effects").fetchone() == (0,)
    assert con.execute("SELECT count(*) FROM resource_attempts").fetchone() == (0,)
    assert con.execute("SELECT count(*) FROM journal_events").fetchone() == (0,)
    con.close()
    from tests.security.test_recovery_04c import _reconcile

    recovered = _reconcile(routine_db, original)
    assert recovered.outcome == "INDETERMINATE"
    assert recovered.reason_code == "ORIGINAL_HISTORY_CONTINUITY_UNPROVED"
    assert continuity_path(routine_db).exists()


def test_witness_fsync_failure_rolls_back_without_false_negative(
    routine_db, monkeypatch
):
    import scoperelay.storage.continuity as continuity
    from tests.security.test_recovery_04c import _reconcile

    original = _identity(routine_db)

    def fail_fsync(_fd):
        raise OSError("injected witness durability failure")

    with ApplicationWriter.start(routine_db) as owner:
        with monkeypatch.context() as fault:
            fault.setattr(continuity.os, "fsync", fail_fsync)
            result = ProtectedCommunicationResource(
                routine_db, owner
            ).admit_material_action(
                "action-01",
                "material-01",
                None,
                ResourceCallerContext("ep-01", EPOCH),
                at=NOW,
            )
    assert result.outcome == "TECHNICAL_ERROR"
    con = open_existing(routine_db)
    assert con.execute("SELECT count(*) FROM queue_effects").fetchone() == (0,)
    con.close()
    assert _reconcile(routine_db, original).outcome == "INDETERMINATE"


@pytest.mark.xfail(
    strict=True,
    reason="joint DB+witness rollback lacks an independent continuity anchor",
)
def test_joint_old_database_and_witness_restore_must_not_prove_noncommit(
    routine_db, tmp_path
):
    from tests.security.test_recovery_04c import _reconcile

    original = _identity(routine_db)
    snapshot = tmp_path / "old.db"
    source = open_existing(routine_db)
    target = sqlite3.connect(snapshot)
    source.backup(target)
    target.close()
    source.close()
    old_witness = tmp_path / "old.continuity"
    shutil.copyfile(continuity_path(routine_db), old_witness)
    with ApplicationWriter.start(routine_db) as owner:
        accepted = ProtectedCommunicationResource(
            routine_db, owner
        ).admit_material_action(
            "action-01",
            "material-01",
            None,
            ResourceCallerContext("ep-01", EPOCH),
            at=NOW,
        )
    assert accepted.outcome == "ACCEPTED_COMMITTED"
    old = sqlite3.connect(snapshot)
    current = open_existing(routine_db)
    old.backup(current)
    current.close()
    old.close()
    shutil.copyfile(old_witness, continuity_path(routine_db))
    assert _reconcile(routine_db, original).outcome == "INDETERMINATE"
