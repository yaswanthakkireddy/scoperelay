"""Deterministic trusted supervisor simulation; no model or browser authentication claim."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from hashlib import sha256
import sqlite3

import pytest

from scoperelay.storage.database import check_integrity, open_existing
from scoperelay.storage.fixture_loader import EPOCH, seed_synthetic
from scoperelay.storage.schema import initialize
from scoperelay.storage.writer_owner import ApplicationWriter
from scoperelay.trusted.approval import ApprovalError, LocalSupervisorService
from scoperelay.trusted.contracts import CandidateRequestV1, TrustedTask
from scoperelay.trusted.material_store import freeze_escalation_test_preview
from scoperelay.trusted.resource import (
    ProtectedCommunicationResource,
    ResourceCallerContext,
    ResourceEvidenceError,
)
import scoperelay.trusted.resource as resource_module


NOW = datetime(2026, 10, 14, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def escalation(tmp_path: Path):
    path = tmp_path / "escalation.db"
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
            "2026-10-14T00:00:00.000000Z",
            None,
        ),
    )
    s = con.execute(
        "SELECT source_id,source_version,body_text FROM ticket_sources WHERE ticket_id='T-006'"
    ).fetchone()
    candidate = CandidateRequestV1.from_mapping(
        {
            "candidate_id": "deterministic",
            "ticket_id": "T-006",
            "operation_hint": "SANITIZED_INTERNAL_ESCALATION",
            "order_ref_text": None,
            "requested_recipient_text": None,
            "content_class_hint": "STATUS_ONLY",
            "source_spans": [
                {
                    "source_id": s[0],
                    "source_version": s[1],
                    "start": 0,
                    "end": len(s[2]),
                    "excerpt": s[2],
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
    freeze_escalation_test_preview(
        con,
        TrustedTask("ep-06", 1, "STATUS_CONFLICT"),
        candidate,
        at=NOW,
        material_action_id="material-06",
        action_id="action-06",
        approval_id="approval-06",
        expires_at=NOW + timedelta(hours=1),
    )
    con.close()
    return path


def _admit(path: Path):
    with ApplicationWriter.start(path, timeout=5.0) as owner:
        return ProtectedCommunicationResource(path, owner).admit_material_action(
            "action-06",
            "material-06",
            "approval-06",
            ResourceCallerContext("ep-06", EPOCH),
            at=NOW,
        )


def test_service_approved_sc06_exact_one_use(escalation: Path):
    svc = LocalSupervisorService(escalation)
    token = svc.issue_trusted_local_session(at=NOW)
    preview = svc.review(token, "approval-06", at=NOW)
    assert (
        preview.status == "PENDING"
        and preview.recipient_contact_id == "internal-support"
    )
    assert _admit(escalation).outcome == "EXPLICIT_POLICY_DENIAL"
    approved = svc.decide(
        token,
        "approval-06",
        expected_version=1,
        expected_digest=preview.material_sha256,
        decision="APPROVE",
        at=NOW,
    )
    assert approved.status == "APPROVED" and approved.version == 2
    accepted = _admit(escalation)
    assert accepted.outcome == "ACCEPTED_COMMITTED"
    replay = _admit(escalation)
    assert replay.replayed and replay.effect_id == accepted.effect_id
    con = open_existing(escalation)
    assert con.execute("SELECT count(*) FROM queue_effects").fetchone() == (1,)
    assert con.execute(
        "SELECT recipient_contact_id,body_text FROM queue_effects"
    ).fetchone() == (preview.recipient_contact_id, preview.rendered_body)
    assert con.execute(
        "SELECT status,supervisor_id,consumed_effect_id FROM escalation_approvals"
    ).fetchone() == ("CONSUMED", "supervisor-1", accepted.effect_id)
    assert con.execute(
        "SELECT count(*) FROM journal_events WHERE event_type='ACCEPTED_COMMITTED'"
    ).fetchone() == (1,)
    check_integrity(con)
    con.close()


def test_escalation_abort_does_not_consume_approval(escalation: Path, monkeypatch):
    svc = LocalSupervisorService(escalation)
    token = svc.issue_trusted_local_session(at=NOW)
    digest = svc.review(token, "approval-06", at=NOW).material_sha256
    svc.decide(
        token,
        "approval-06",
        expected_version=1,
        expected_digest=digest,
        decision="APPROVE",
        at=NOW,
    )

    def fault(stage: str):
        if stage == "after_journal":
            raise RuntimeError("before commit")

    monkeypatch.setattr(resource_module, "_checkpoint", fault)
    result = _admit(escalation)
    assert result.outcome == "TECHNICAL_ERROR"
    con = open_existing(escalation)
    assert con.execute(
        "SELECT status,consumed_effect_id FROM escalation_approvals"
    ).fetchone() == ("APPROVED", None)
    assert con.execute("SELECT count(*) FROM queue_effects").fetchone() == (0,)
    assert con.execute("SELECT count(*) FROM journal_events").fetchone() == (0,)
    con.close()


def test_concurrent_admission_consumes_approval_once(escalation: Path):
    svc = LocalSupervisorService(escalation)
    token = svc.issue_trusted_local_session(at=NOW)
    digest = svc.review(token, "approval-06", at=NOW).material_sha256
    svc.decide(
        token,
        "approval-06",
        expected_version=1,
        expected_digest=digest,
        decision="APPROVE",
        at=NOW,
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: _admit(escalation), range(2)))
    assert (
        sum(r.outcome == "ACCEPTED_COMMITTED" and not r.replayed for r in results) == 1
    )
    assert sum(r.replayed for r in results) == 1
    assert results[0].effect_id == results[1].effect_id


def test_historical_escalation_receipt_requires_consumed_approval(escalation: Path):
    svc = LocalSupervisorService(escalation)
    token = svc.issue_trusted_local_session(at=NOW)
    digest = svc.review(token, "approval-06", at=NOW).material_sha256
    svc.decide(
        token,
        "approval-06",
        expected_version=1,
        expected_digest=digest,
        decision="APPROVE",
        at=NOW,
    )
    assert _admit(escalation).outcome == "ACCEPTED_COMMITTED"
    con = open_existing(escalation)
    con.execute("DELETE FROM escalation_approvals WHERE approval_id='approval-06'")
    con.close()
    with pytest.raises(ResourceEvidenceError):
        ProtectedCommunicationResource(escalation).accepted_receipt(
            "action-06", ResourceCallerContext("ep-06", EPOCH)
        )


def test_rejected_and_forged_session_do_not_authorize(escalation: Path):
    svc = LocalSupervisorService(escalation)
    token = svc.issue_trusted_local_session(at=NOW)
    with pytest.raises(ApprovalError):
        svc.decide(
            "supervisor-1",
            "approval-06",
            expected_version=1,
            expected_digest="x",
            decision="APPROVE",
            at=NOW,
        )
    preview = svc.review(token, "approval-06", at=NOW)
    with pytest.raises(ApprovalError):
        svc.decide(
            token,
            "approval-06",
            expected_version=1,
            expected_digest="f" * 64,
            decision="APPROVE",
            at=NOW,
        )
    svc.decide(
        token,
        "approval-06",
        expected_version=1,
        expected_digest=preview.material_sha256,
        decision="REJECT",
        at=NOW,
    )
    assert _admit(escalation).outcome == "EXPLICIT_POLICY_DENIAL"


def test_wrong_org_and_role_context_cannot_decide(escalation: Path):
    svc = LocalSupervisorService(escalation)
    token = "wrong-org-session-token-with-enough-entropy-for-test"
    con = open_existing(escalation)
    con.execute("INSERT INTO organizations VALUES ('other-org','Other',1,1,1)")
    con.execute(
        "INSERT INTO principals VALUES ('other-supervisor','other-org','SUPERVISOR','Other',1,1)"
    )
    con.execute(
        "INSERT INTO operator_sessions VALUES (?,?,?,?,?,?,?,?)",
        (
            sha256(token.encode()).hexdigest(),
            "other-supervisor",
            "SUPERVISOR",
            "unused",
            "2026-10-14T00:00:00.000000Z",
            "2026-10-14T01:00:00.000000Z",
            None,
            1,
        ),
    )
    con.close()
    with pytest.raises(ApprovalError):
        svc.review(token, "approval-06", at=NOW)
    valid = svc.issue_trusted_local_session(at=NOW)
    con = open_existing(escalation)
    con.execute(
        "UPDATE operator_sessions SET role_context='EVALUATOR' WHERE session_id_hash=?",
        (sha256(valid.encode()).hexdigest(),),
    )
    con.close()
    with pytest.raises(ApprovalError):
        svc.review(valid, "approval-06", at=NOW)


def test_concurrent_decisions_only_one_wins(escalation: Path):
    svc = LocalSupervisorService(escalation)
    token = svc.issue_trusted_local_session(at=NOW)
    digest = svc.review(token, "approval-06", at=NOW).material_sha256

    def decide(kind: str):
        try:
            return svc.decide(
                token,
                "approval-06",
                expected_version=1,
                expected_digest=digest,
                decision=kind,
                at=NOW,
            ).status
        except ApprovalError:
            return "CONFLICT"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(decide, ("APPROVE", "REJECT")))
    assert results.count("CONFLICT") == 1
    assert len(set(results) - {"CONFLICT"}) == 1


def test_revoked_supervisor_and_approval(escalation: Path):
    svc = LocalSupervisorService(escalation)
    token = svc.issue_trusted_local_session(at=NOW)
    digest = svc.review(token, "approval-06", at=NOW).material_sha256
    svc.decide(
        token,
        "approval-06",
        expected_version=1,
        expected_digest=digest,
        decision="APPROVE",
        at=NOW,
    )
    svc.revoke(token, "approval-06", expected_version=2, at=NOW)
    assert _admit(escalation).outcome == "EXPLICIT_POLICY_DENIAL"


def test_supervisor_entitlement_revoked_after_approval(escalation: Path):
    svc = LocalSupervisorService(escalation)
    token = svc.issue_trusted_local_session(at=NOW)
    digest = svc.review(token, "approval-06", at=NOW).material_sha256
    svc.decide(
        token,
        "approval-06",
        expected_version=1,
        expected_digest=digest,
        decision="APPROVE",
        at=NOW,
    )
    con = open_existing(escalation)
    con.execute(
        "UPDATE principals SET active=0,version=2 WHERE principal_id='supervisor-1'"
    )
    con.close()
    assert _admit(escalation).outcome == "EXPLICIT_POLICY_DENIAL"
    with pytest.raises(ApprovalError):
        svc.review(token, "approval-06", at=NOW)


def test_expired_pending_cannot_be_decided(escalation: Path):
    svc = LocalSupervisorService(escalation)
    token = svc.issue_trusted_local_session(at=NOW)
    digest = svc.review(token, "approval-06", at=NOW).material_sha256
    later = NOW + timedelta(hours=2)
    token = svc.issue_trusted_local_session(at=later)
    with pytest.raises(ApprovalError):
        svc.decide(
            token,
            "approval-06",
            expected_version=1,
            expected_digest=digest,
            decision="APPROVE",
            at=later,
        )


def test_approval_digest_mismatch_is_rejected(escalation: Path):
    svc = LocalSupervisorService(escalation)
    token = svc.issue_trusted_local_session(at=NOW)
    con = open_existing(escalation)
    # FK prevents even a test-only approval digest rewrite.
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "UPDATE escalation_approvals SET material_sha256=? WHERE approval_id='approval-06'",
            ("f" * 64,),
        )
    con.close()
    assert svc.review(token, "approval-06", at=NOW).status == "PENDING"


@pytest.mark.parametrize(
    "column",
    [
        "recipient_contact_id",
        "operation_class",
        "template_id",
        "rendered_body",
        "field_values_json",
        "canonical_json",
        "material_sha256",
        "business_key",
        "source_version",
        "canonical_schema_version",
        "account_id",
        "order_id",
    ],
)
def test_frozen_material_columns_cannot_be_updated(escalation: Path, column: str):
    con = open_existing(escalation)
    with pytest.raises(sqlite3.IntegrityError, match="immutable material action"):
        con.execute(
            f"UPDATE material_actions SET {column}={column} WHERE material_action_id='material-06'"
        )
    with pytest.raises(sqlite3.IntegrityError, match="immutable material action"):
        con.execute(
            "DELETE FROM material_actions WHERE material_action_id='material-06'"
        )
    con.close()
