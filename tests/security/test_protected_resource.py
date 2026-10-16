"""Real file-backed API-108 effects. No model traffic or external delivery."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from scoperelay.storage.database import check_integrity, open_existing
from scoperelay.storage.fixture_loader import EPOCH, seed_synthetic
from scoperelay.storage.schema import initialize
from scoperelay.storage.source_spans import source_digest
from scoperelay.storage.writer_owner import ApplicationWriter, WriterBusy
from scoperelay.trusted.contracts import CandidateRequestV1, TrustedTask
from scoperelay.trusted.material_store import (
    freeze_escalation_test_preview,
    freeze_routine_test_preview,
)
from scoperelay.trusted.resource import (
    ProtectedCommunicationResource,
    ResourceCallerContext,
    ResourceEvidenceError,
)
import scoperelay.trusted.resource as resource_module


ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 10, 14, tzinfo=timezone.utc)
STAMP = "2026-10-14T00:00:00.000000Z"


def _candidate(
    con: sqlite3.Connection, ticket: str, *, operation: str = "ROUTINE_ORDER_STATUS"
) -> CandidateRequestV1:
    source = con.execute(
        "SELECT source_id,source_version,body_text FROM ticket_sources WHERE ticket_id=?",
        (ticket,),
    ).fetchone()
    return CandidateRequestV1.from_mapping(
        {
            "candidate_id": "deterministic",
            "ticket_id": ticket,
            "operation_hint": operation,
            "order_ref_text": "ORD-101"
            if operation == "ROUTINE_ORDER_STATUS"
            else None,
            "requested_recipient_text": None,
            "content_class_hint": "STATUS_ONLY",
            "source_spans": [
                {
                    "source_id": source[0],
                    "source_version": source[1],
                    "start": 0,
                    "end": len(source[2]),
                    "excerpt": source[2],
                }
            ],
            "uncertainty": {
                "ambiguous": False,
                "quoted_only": False,
                "conflicting_references": [],
            },
            "other_candidates": [],
        }
    )


@pytest.fixture
def routine_db(tmp_path: Path) -> Path:
    path = tmp_path / "resource.db"
    initialize(path)
    seed_synthetic(
        path,
        ROOT / "fixtures/scenarios/source_texts.json",
        ROOT / "fixtures/scenarios/oracle_labels.json",
    )
    con = open_existing(path)
    con.execute(
        "INSERT INTO episodes VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            "ep-01",
            "SC-01",
            EPOCH,
            "T-001",
            1,
            "grant-01",
            "DETERMINISTIC_COMPONENT_TEST",
            "STARTED",
            STAMP,
            None,
        ),
    )
    freeze_routine_test_preview(
        con,
        TrustedTask("ep-01", 1),
        _candidate(con, "T-001"),
        at=NOW,
        material_action_id="material-01",
        action_id="action-01",
        purpose="STATUS_WITH_ETA",
        template_id="STATUS_T2",
        selected_field_ids=("order_id", "order_status", "customer_visible_eta"),
    )
    check_integrity(con)
    con.close()
    return path


def _admit(
    path: Path,
    *,
    action: str = "action-01",
    material: str = "material-01",
    episode: str = "ep-01",
    approval: str | None = None,
):
    try:
        with ApplicationWriter.start(path, timeout=5.0) as owner:
            return ProtectedCommunicationResource(path, owner).admit_material_action(
                action,
                material,
                approval,
                ResourceCallerContext(episode, EPOCH),
                at=NOW,
            )
    except (FileNotFoundError, WriterBusy):
        return ProtectedCommunicationResource(path).admit_material_action(
            action, material, approval, ResourceCallerContext(episode, EPOCH), at=NOW
        )


def test_real_routine_commit_receipt_journal_and_replay(routine_db: Path) -> None:
    first = _admit(routine_db)
    assert (
        first.outcome == "ACCEPTED_COMMITTED"
        and first.evidence_complete
        and not first.replayed
    )
    assert first.effect_id and first.attempt_id and first.journal_event_seq
    second = _admit(routine_db)
    assert second.outcome == "ACCEPTED_COMMITTED" and second.replayed
    assert (second.effect_id, second.attempt_id, second.journal_event_seq) == (
        first.effect_id,
        first.attempt_id,
        first.journal_event_seq,
    )
    con = open_existing(routine_db)
    assert con.execute("SELECT count(*) FROM queue_effects").fetchone() == (1,)
    assert con.execute("SELECT count(*) FROM resource_attempts").fetchone() == (1,)
    assert con.execute("SELECT count(*) FROM journal_events").fetchone() == (1,)
    assert con.execute(
        "SELECT recipient_contact_id,body_text FROM queue_effects"
    ).fetchone() == (
        "contact-c1",
        "Order ORD-101 is IN_TRANSIT. Estimated arrival: 2026-10-20.",
    )
    assert con.execute(
        "SELECT outcome,effect_id FROM resource_attempts"
    ).fetchone() == ("ACCEPTED_COMMITTED", first.effect_id)
    assert con.execute(
        "SELECT event_type,effect_id FROM journal_events"
    ).fetchone() == ("ACCEPTED_COMMITTED", first.effect_id)
    check_integrity(con)
    con.close()


@pytest.mark.parametrize(
    "mutation,reason",
    [
        (
            "UPDATE manager_grants SET revoked_at='2026-10-14T00:00:00Z',version=2 WHERE grant_id='grant-01'",
            "GRANT_REVOKED_OR_EXPIRED",
        ),
        (
            "UPDATE manager_grants SET expires_at='2020-01-01T00:00:00Z' WHERE grant_id='grant-01'",
            "GRANT_REVOKED_OR_EXPIRED",
        ),
        (
            "UPDATE manager_grants SET operation_class='SANITIZED_INTERNAL_ESCALATION' WHERE grant_id='grant-01'",
            "GRANT_SCOPE_VERSION_OR_OPERATION_INVALID",
        ),
        (
            "UPDATE tickets SET assignment_version=2 WHERE ticket_id='T-001'",
            "STALE_TASK_OR_ORG",
        ),
        (
            "UPDATE contacts SET active=0,version=2 WHERE contact_id='contact-c1'",
            "RECIPIENT_CHANGED_OR_INVALID",
        ),
        (
            "UPDATE orders SET order_status='DELAYED',version=2 WHERE order_id='ORD-101'",
            "ORDER_OWNERSHIP_OR_VERSION_CHANGED",
        ),
        (
            "UPDATE orders SET customer_visible_eta='2026-10-21',version=2 WHERE order_id='ORD-101'",
            "ORDER_OWNERSHIP_OR_VERSION_CHANGED",
        ),
        (
            "UPDATE org_policy SET version=2 WHERE org_id='org_demo'",
            "POLICY_CHANGED_OR_OPERATION_FORBIDDEN",
        ),
        (
            "UPDATE tickets SET current_source_version=2 WHERE ticket_id='T-001'",
            "STALE_TASK_OR_ORG",
        ),
        (
            "UPDATE orders SET account_id='A2',version=2 WHERE order_id='ORD-101'",
            "WRONG_OWNER_OR_RECIPIENT",
        ),
    ],
)
def test_current_state_denial_has_real_attempt_and_journal(
    routine_db: Path, mutation: str, reason: str
) -> None:
    con = open_existing(routine_db)
    con.execute(
        mutation
    )  # Fixed test-only SQL, never untrusted input or production path.
    con.close()
    result = _admit(routine_db)
    assert (result.outcome, result.reason_code, result.evidence_complete) == (
        "EXPLICIT_POLICY_DENIAL",
        reason,
        True,
    )
    con = open_existing(routine_db)
    assert con.execute(
        "SELECT outcome,reason_code FROM resource_attempts"
    ).fetchone() == ("EXPLICIT_POLICY_DENIAL", reason)
    assert con.execute(
        "SELECT event_type,reason_code FROM journal_events"
    ).fetchone() == ("EXPLICIT_POLICY_DENIAL", reason)
    assert con.execute("SELECT count(*) FROM queue_effects").fetchone() == (0,)
    check_integrity(con)
    con.close()


def test_body_and_field_tamper_denied(routine_db: Path) -> None:
    con = open_existing(routine_db)
    with pytest.raises(sqlite3.IntegrityError, match="immutable material action"):
        con.execute(
            "UPDATE material_actions SET rendered_body='internal_notes: secret' WHERE material_action_id='material-01'"
        )
    con.close()
    assert _admit(routine_db).outcome == "ACCEPTED_COMMITTED"


def test_concurrent_identical_submissions_one_effect(routine_db: Path) -> None:
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: _admit(routine_db), range(4)))
    assert (
        sum(r.outcome == "ACCEPTED_COMMITTED" and not r.replayed for r in results) == 1
    )
    assert sum(r.replayed for r in results) == 3
    con = open_existing(routine_db)
    assert con.execute("SELECT count(*) FROM queue_effects").fetchone() == (1,)
    check_integrity(con)
    con.close()


def _second_same_business(path: Path) -> None:
    con = open_existing(path)
    freeze_routine_test_preview(
        con,
        TrustedTask("ep-01", 1),
        _candidate(con, "T-001"),
        at=NOW,
        material_action_id="material-02",
        action_id="action-02",
        purpose="STATUS_WITH_ETA",
        template_id="STATUS_T2",
        selected_field_ids=("order_id", "order_status", "customer_visible_eta"),
    )
    con.close()


def test_dual_identity_conflicts_have_no_second_effect(routine_db: Path) -> None:
    _second_same_business(routine_db)
    first = _admit(routine_db)
    assert first.outcome == "ACCEPTED_COMMITTED"
    new_id = _admit(routine_db, action="action-02", material="material-02")
    assert (new_id.outcome, new_id.reason_code, new_id.original_effect_id) == (
        "EXPLICIT_POLICY_DENIAL",
        "BUSINESS_ALREADY_COMMITTED",
        first.effect_id,
    )
    changed = _admit(routine_db, action="action-01", material="material-02")
    assert (changed.outcome, changed.reason_code) == (
        "EXPLICIT_POLICY_DENIAL",
        "ACTION_ID_CONFLICT",
    )
    con = open_existing(routine_db)
    assert con.execute("SELECT count(*) FROM queue_effects").fetchone() == (1,)
    assert con.execute(
        "SELECT count(*) FROM journal_events WHERE event_type='EXPLICIT_POLICY_DENIAL'"
    ).fetchone() == (2,)
    check_integrity(con)
    con.close()


def test_concurrent_different_actions_one_business_effect(routine_db: Path) -> None:
    _second_same_business(routine_db)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(_admit, routine_db, action=a, material=m)
            for a, m in (("action-01", "material-01"), ("action-02", "material-02"))
        ]
        results = [f.result() for f in futures]
    assert sorted(r.outcome for r in results) == [
        "ACCEPTED_COMMITTED",
        "EXPLICIT_POLICY_DENIAL",
    ]
    con = open_existing(routine_db)
    assert con.execute("SELECT count(*) FROM queue_effects").fetchone() == (1,)
    check_integrity(con)
    con.close()


def test_pending_escalation_denied_then_test_approved_consumed(tmp_path: Path) -> None:
    path = tmp_path / "approval.db"
    initialize(path)
    seed_synthetic(
        path,
        ROOT / "fixtures/scenarios/source_texts.json",
        ROOT / "fixtures/scenarios/oracle_labels.json",
    )
    con = open_existing(path)
    con.execute(
        "INSERT INTO episodes VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            "ep-06",
            "SC-06",
            EPOCH,
            "T-006",
            1,
            "grant-06",
            "DETERMINISTIC_COMPONENT_TEST",
            "STARTED",
            STAMP,
            None,
        ),
    )
    freeze_escalation_test_preview(
        con,
        TrustedTask("ep-06", 1, "STATUS_CONFLICT"),
        _candidate(con, "T-006", operation="SANITIZED_INTERNAL_ESCALATION"),
        at=NOW,
        material_action_id="material-06",
        action_id="action-06",
        approval_id="approval-06",
        expires_at=NOW + timedelta(hours=1),
    )
    con.close()
    pending = _admit(
        path,
        action="action-06",
        material="material-06",
        episode="ep-06",
        approval="approval-06",
    )
    assert (pending.outcome, pending.reason_code) == (
        "EXPLICIT_POLICY_DENIAL",
        "APPROVAL_NOT_APPROVED_OR_ALREADY_USED",
    )
    con = open_existing(path)
    assert con.execute("SELECT count(*) FROM queue_effects").fetchone() == (0,)
    # Isolated disposable-DB setup, NOT an authenticated supervisor service.
    con.execute(
        "UPDATE escalation_approvals SET status='APPROVED',supervisor_id='supervisor-1',"
        "supervisor_version=1,decision='APPROVE',decided_at=?,approved_at=?,version=2 "
        "WHERE approval_id='approval-06'",
        (STAMP, STAMP),
    )
    con.close()
    accepted = _admit(
        path,
        action="action-06",
        material="material-06",
        episode="ep-06",
        approval="approval-06",
    )
    assert accepted.outcome == "ACCEPTED_COMMITTED"
    con = open_existing(path)
    assert con.execute(
        "SELECT status,consumed_effect_id FROM escalation_approvals"
    ).fetchone() == ("CONSUMED", accepted.effect_id)
    assert con.execute("SELECT count(*) FROM queue_effects").fetchone() == (1,)
    check_integrity(con)
    con.close()


@pytest.mark.parametrize(
    "stage",
    [
        "before_begin",
        "after_begin",
        "after_grant",
        "after_ownership",
        "after_material",
        "after_attempt",
        "after_queue",
        "after_journal",
    ],
)
def test_precommit_fault_rolls_back_without_false_denial(
    routine_db: Path, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    def fault(current: str) -> None:
        if current == stage:
            raise RuntimeError("injected precommit fault")

    monkeypatch.setattr(resource_module, "_checkpoint", fault)
    result = _admit(routine_db)
    assert result.outcome == "TECHNICAL_ERROR" and not result.evidence_complete
    con = open_existing(routine_db)
    for table in ("resource_attempts", "queue_effects", "journal_events"):
        assert con.execute(f"SELECT count(*) FROM {table}").fetchone() == (0,)
    check_integrity(con)
    con.close()


def test_lost_ack_after_commit_preserves_uncertainty(
    routine_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fault(stage: str) -> None:
        if stage == "after_commit":
            raise RuntimeError("response lost after commit")

    monkeypatch.setattr(resource_module, "_checkpoint", fault)
    result = _admit(routine_db)
    assert result.outcome == "INDETERMINATE" and result.effect_id is None
    assert (
        result.attempt_id
        and result.business_key
        and result.material_action_id == "material-01"
    )
    con = open_existing(routine_db)
    assert con.execute("SELECT count(*) FROM queue_effects").fetchone() == (1,)
    assert con.execute("SELECT count(*) FROM journal_events").fetchone() == (1,)
    check_integrity(con)
    con.close()


def test_mutation_commits_first_then_admission_denies(routine_db: Path) -> None:
    writer = open_existing(routine_db)
    writer.execute("BEGIN IMMEDIATE")
    writer.execute(
        "UPDATE manager_grants SET revoked_at=?,version=2 WHERE grant_id='grant-01'",
        (STAMP,),
    )
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(_admit, routine_db)
        writer.execute("COMMIT")
        result = pending.result()
    writer.close()
    assert (result.outcome, result.reason_code) == (
        "EXPLICIT_POLICY_DENIAL",
        "GRANT_REVOKED_OR_EXPIRED",
    )


def test_admission_commits_first_then_revocation_preserves_history(
    routine_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    held = Event()
    release = Event()

    def checkpoint(stage: str) -> None:
        if stage == "after_grant":
            held.set()
            assert release.wait(4)

    monkeypatch.setattr(resource_module, "_checkpoint", checkpoint)
    with ThreadPoolExecutor(max_workers=2) as pool:
        admission = pool.submit(_admit, routine_db)
        assert held.wait(4)

        def revoke() -> None:
            con = open_existing(routine_db)
            con.execute("BEGIN IMMEDIATE")
            con.execute(
                "UPDATE manager_grants SET revoked_at=?,version=2 WHERE grant_id='grant-01'",
                (STAMP,),
            )
            con.execute("COMMIT")
            con.close()

        mutation = pool.submit(revoke)
        release.set()
        accepted = admission.result()
        mutation.result()
    assert accepted.outcome == "ACCEPTED_COMMITTED"
    replay = _admit(routine_db)
    assert replay.replayed and replay.effect_id == accepted.effect_id


def test_independently_new_trusted_interaction_can_commit(routine_db: Path) -> None:
    assert _admit(routine_db).outcome == "ACCEPTED_COMMITTED"
    con = open_existing(routine_db)
    con.execute(
        "INSERT INTO tickets VALUES (?,?,?,?,?,?,?)",
        ("T-NEW", "org_demo", "C1", None, 1, 1, 1),
    )
    con.execute(
        "INSERT INTO manager_grants VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            "grant-new",
            "org_demo",
            "manager-1",
            "ROUTINE_ORDER_STATUS",
            "T-NEW",
            1,
            1,
            "2030-01-01T00:00:00Z",
            None,
            STAMP,
        ),
    )
    con.execute(
        "UPDATE tickets SET assigned_grant_id='grant-new' WHERE ticket_id='T-NEW'"
    )
    text = "Please send a current status for my order ORD-101."
    con.execute(
        "INSERT INTO ticket_sources VALUES (?,?,?,?,?,?,?)",
        ("source-new", "T-NEW", 1, text, source_digest(text), "BI-NEW", STAMP),
    )
    con.execute(
        "INSERT INTO scenarios VALUES (?,?,?,?,?,?)",
        (
            "SC-NEW",
            "task03-source-draft-v1",
            EPOCH,
            "Independent synthetic interaction",
            "{}",
            STAMP,
        ),
    )
    con.execute(
        "INSERT INTO episodes VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            "ep-new",
            "SC-NEW",
            EPOCH,
            "T-NEW",
            1,
            "grant-new",
            "DETERMINISTIC_COMPONENT_TEST",
            "STARTED",
            STAMP,
            None,
        ),
    )
    freeze_routine_test_preview(
        con,
        TrustedTask("ep-new", 1),
        _candidate(con, "T-NEW"),
        at=NOW,
        material_action_id="material-new",
        action_id="action-new",
        purpose="STATUS_BRIEF",
        template_id="STATUS_T1",
        selected_field_ids=("order_id", "order_status"),
    )
    con.close()
    second = _admit(
        routine_db, action="action-new", material="material-new", episode="ep-new"
    )
    assert second.outcome == "ACCEPTED_COMMITTED"
    con = open_existing(routine_db)
    assert con.execute("SELECT count(*) FROM queue_effects").fetchone() == (2,)
    assert con.execute(
        "SELECT count(DISTINCT business_key) FROM queue_effects"
    ).fetchone() == (2,)
    check_integrity(con)
    con.close()


def test_lock_contention_is_technical_not_denial(routine_db: Path) -> None:
    writer = open_existing(routine_db)
    writer.execute("BEGIN IMMEDIATE")
    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(_admit, routine_db).result()
    writer.execute("ROLLBACK")
    writer.close()
    assert (
        result.outcome == "TECHNICAL_ERROR" and result.reason_code == "OperationalError"
    )
    con = open_existing(routine_db)
    assert con.execute("SELECT count(*) FROM resource_attempts").fetchone() == (0,)
    assert con.execute("SELECT count(*) FROM journal_events").fetchone() == (0,)
    con.close()


def test_denial_journal_failure_cannot_claim_explicit_policy_denial(
    routine_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    con = open_existing(routine_db)
    con.execute(
        "UPDATE manager_grants SET revoked_at=?,version=2 WHERE grant_id='grant-01'",
        (STAMP,),
    )
    con.close()

    def fault(stage: str) -> None:
        if stage == "after_attempt":
            raise RuntimeError("denial recording failed")

    monkeypatch.setattr(resource_module, "_checkpoint", fault)
    result = _admit(routine_db)
    assert result.outcome == "TECHNICAL_ERROR" and not result.evidence_complete
    con = open_existing(routine_db)
    assert con.execute("SELECT count(*) FROM resource_attempts").fetchone() == (0,)
    assert con.execute("SELECT count(*) FROM journal_events").fetchone() == (0,)
    con.close()


def test_missing_authoritative_db_is_technical_not_recreated(tmp_path: Path) -> None:
    path = tmp_path / "never-created.db"
    result = _admit(path)
    assert result.outcome == "TECHNICAL_ERROR" and not path.exists()


def test_receipt_reader_rejects_missing_or_tampered_witness(routine_db: Path) -> None:
    accepted = _admit(routine_db)
    resource = ProtectedCommunicationResource(routine_db)
    historical = resource.accepted_receipt(
        "action-01", ResourceCallerContext("ep-01", EPOCH)
    )
    assert historical.effect_id == accepted.effect_id and historical.replayed
    with pytest.raises(ResourceEvidenceError):
        resource.accepted_receipt("action-01", ResourceCallerContext("wrong", EPOCH))
    con = open_existing(routine_db)
    with pytest.raises(sqlite3.IntegrityError, match="immutable material action"):
        con.execute(
            "UPDATE material_actions SET canonical_json='{}' WHERE material_action_id='material-01'"
        )
    con.execute(
        "UPDATE journal_events SET event_type='CORRUPTED' WHERE action_id='action-01'"
    )
    con.close()
    with pytest.raises(ResourceEvidenceError):
        resource.accepted_receipt("action-01", ResourceCallerContext("ep-01", EPOCH))
    # This is direct test-only DB tampering, not an application mutation path.


@pytest.mark.parametrize(
    "mutation,reason",
    [
        (
            "UPDATE manager_grants SET revoked_at='2026-10-14T00:00:00Z',version=2 WHERE grant_id='grant-01'",
            "GRANT_REVOKED_OR_EXPIRED",
        ),
        (
            "UPDATE contacts SET active=0,version=2 WHERE contact_id='contact-c1'",
            "RECIPIENT_CHANGED_OR_INVALID",
        ),
        (
            "UPDATE orders SET account_id='A2',version=2 WHERE order_id='ORD-101'",
            "WRONG_OWNER_OR_RECIPIENT",
        ),
    ],
)
def test_competing_mutation_commits_first_and_is_seen(
    routine_db: Path, mutation: str, reason: str
) -> None:
    writer = open_existing(routine_db)
    writer.execute("BEGIN IMMEDIATE")
    writer.execute(mutation)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(_admit, routine_db)
        writer.execute("COMMIT")
        result = pending.result()
    writer.close()
    assert result.outcome == "EXPLICIT_POLICY_DENIAL" and result.reason_code == reason
