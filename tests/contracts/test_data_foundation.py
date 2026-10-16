import json
from pathlib import Path
import sqlite3
from hashlib import sha256

import pytest

from scoperelay.evaluation.oracle import read_truth
from scoperelay.storage.agent_reads import AgentReadRepository, ScopeError
from scoperelay.storage.database import DatabaseError, check_integrity, open_existing
from scoperelay.storage.fixture_loader import EPOCH, business_key, seed_synthetic
from scoperelay.storage.schema import _migration_source, initialize
from scoperelay.storage.source_spans import SourceReferenceError
from scoperelay.storage.transaction import immediate
from scoperelay.storage.transaction import connect


ROOT = Path(__file__).resolve().parents[2]
SOURCES = ROOT / "fixtures" / "scenarios" / "source_texts.json"
ORACLE = ROOT / "fixtures" / "scenarios" / "oracle_labels.json"
STAMP = "2026-10-14T00:00:00.000000Z"


@pytest.fixture
def seeded(tmp_path: Path) -> Path:
    path = tmp_path / "synthetic.db"
    initialize(path)
    assert seed_synthetic(path, SOURCES, ORACLE)
    return path


def test_schema_version_reopen_and_constraints(tmp_path: Path) -> None:
    path = tmp_path / "schema.db"
    initialize(path)
    initialize(path)
    con = open_existing(path)
    assert con.execute("PRAGMA user_version").fetchone() == (4,)
    assert con.execute("PRAGMA foreign_keys").fetchone() == (1,)
    assert con.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert con.execute("PRAGMA synchronous").fetchone() == (2,)
    assert (
        len(
            [
                r
                for r in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name<>'schema_migrations'"
                )
            ]
        )
        == 33
    )
    check_integrity(con)
    con.close()


def test_forward_upgrade_from_verified_v1_preserves_existing_rows(
    tmp_path: Path,
) -> None:
    path = tmp_path / "v1.db"
    con = connect(path)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=FULL")
    source = _migration_source()
    con.execute(
        "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL, sha256 TEXT NOT NULL, applied_at TEXT NOT NULL)"
    )
    con.executescript(source)
    con.execute(
        "INSERT INTO schema_migrations VALUES (1,'001_initial.sql',?,?)",
        (sha256(source.encode()).hexdigest(), STAMP),
    )
    con.execute("PRAGMA user_version=1")
    con.execute("INSERT INTO organizations VALUES ('preserved','Preserved',1,1,1)")
    con.close()
    initialize(path)
    upgraded = open_existing(path)
    assert upgraded.execute("PRAGMA user_version").fetchone() == (4,)
    assert upgraded.execute("SELECT org_id FROM organizations").fetchone() == (
        "preserved",
    )
    assert upgraded.execute("SELECT count(*) FROM database_identity").fetchone() == (1,)
    check_integrity(upgraded)
    upgraded.close()


def test_migration_rollback_and_version_rejection(tmp_path: Path) -> None:
    path = tmp_path / "bad.db"
    with pytest.raises(sqlite3.OperationalError):
        initialize(
            path, source_override=_migration_source() + "\nCREATE TABLE broken(;\n"
        )
    con = open_existing(path)
    assert con.execute("PRAGMA user_version").fetchone() == (0,)
    assert (
        con.execute(
            "SELECT name FROM sqlite_master WHERE name='organizations'"
        ).fetchone()
        is None
    )
    assert (
        con.execute(
            "SELECT name FROM sqlite_master WHERE name='schema_migrations'"
        ).fetchone()
        is None
    )
    con.close()
    with pytest.raises(DatabaseError):
        initialize(path)  # Never silently replace a failed authoritative path.

    good = tmp_path / "good.db"
    initialize(good)
    con = open_existing(good)
    con.execute("UPDATE schema_migrations SET sha256='bad'")
    con.close()
    with pytest.raises(DatabaseError, match="checksum"):
        initialize(good)
    con = open_existing(good)
    con.execute(
        "UPDATE schema_migrations SET sha256=?",
        (__import__("hashlib").sha256(_migration_source().encode()).hexdigest(),),
    )
    con.execute("PRAGMA user_version=99")
    con.close()
    with pytest.raises(DatabaseError, match="version"):
        initialize(good)


def test_fixture_sources_oracle_and_reads(seeded: Path) -> None:
    con = open_existing(seeded)
    assert con.execute("SELECT count(*) FROM ticket_sources").fetchone() == (8,)
    assert con.execute("SELECT count(*) FROM evaluation_truth").fetchone() == (8,)
    assert con.execute("SELECT count(*) FROM customers").fetchone() == (2,)
    assert con.execute("SELECT count(*) FROM orders").fetchone() == (3,)
    assert con.execute("SELECT count(*) FROM queue_effects").fetchone() == (0,)
    assert con.execute("SELECT count(*) FROM journal_events").fetchone() == (0,)
    reader = AgentReadRepository(con)
    _episode(con)
    ticket = reader.assigned_ticket(
        ticket_id="T-001", grant_id="grant-01", episode_id="ep-1"
    )
    assert ticket.body_text.startswith("Hello")
    assert not hasattr(ticket, "expected_branch")
    assert not hasattr(reader, "read_truth")
    assert (
        reader.source_excerpt(
            ticket_id="T-001",
            grant_id="grant-01",
            episode_id="ep-1",
            source_version=1,
            start=0,
            end=5,
            excerpt="Hello",
        )
        == "Hello"
    )
    with pytest.raises((ScopeError, SourceReferenceError)):
        reader.source_excerpt(
            ticket_id="T-001",
            grant_id="grant-01",
            episode_id="ep-1",
            source_version=2,
            start=0,
            end=5,
            excerpt="Hello",
        )
    with pytest.raises(ScopeError):
        reader.assigned_ticket(
            ticket_id="T-001", grant_id="grant-02", episode_id="ep-1"
        )
    assert (
        read_truth(con, "SC-08").expected_intent["expected_branch"]
        == "SEMANTICALLY_UNWANTED_POSSIBLE_ALLOW"
    )
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "UPDATE ticket_sources SET body_text='changed' WHERE ticket_id='T-001'"
        )
    check_integrity(con)
    con.close()
    assert not seed_synthetic(seeded, SOURCES, ORACLE)


def test_fixture_tamper_refused(seeded: Path) -> None:
    con = open_existing(seeded)
    con.execute("UPDATE scenarios SET fixture_epoch='other' WHERE scenario_id='SC-01'")
    con.close()
    with pytest.raises(DatabaseError, match="fixture identity"):
        seed_synthetic(seeded, SOURCES, ORACLE)


def test_business_key_stable_and_scoped() -> None:
    original = business_key(
        org_id="org_demo", interaction_id="BI-01", operation="ROUTINE_ORDER_STATUS"
    )
    assert len(original) == 64
    assert original == business_key(
        org_id="org_demo", interaction_id="BI-01", operation="ROUTINE_ORDER_STATUS"
    )
    assert original != business_key(
        org_id="org_demo", interaction_id="BI-02", operation="ROUTINE_ORDER_STATUS"
    )
    assert original != business_key(
        org_id="other", interaction_id="BI-01", operation="ROUTINE_ORDER_STATUS"
    )
    assert original != business_key(
        org_id="org_demo",
        interaction_id="BI-01",
        operation="SANITIZED_INTERNAL_ESCALATION",
    )
    with pytest.raises(ValueError):
        business_key(org_id="org_demo", interaction_id="BI-01", operation="EXPORT")


def _episode(con: sqlite3.Connection, *, episode: str = "ep-1") -> None:
    con.execute(
        "INSERT INTO episodes VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            episode,
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


def _material(
    con: sqlite3.Connection,
    *,
    material: str = "material-1",
    action: str = "action-1",
    key: str | None = None,
    operation: str = "ROUTINE_ORDER_STATUS",
) -> None:
    key = key or business_key(
        org_id="org_demo", interaction_id="BI-01", operation=operation
    )
    con.execute(
        "INSERT INTO material_actions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            material,
            "ep-1",
            action,
            key,
            1,
            operation,
            "org_demo",
            "grant-01",
            "T-001",
            1,
            "A1",
            "ORD-101",
            "contact-c1",
            "[]",
            "STATUS_T1",
            "Synthetic status",
            "{}",
            "{}",
            "a" * 64,
            STAMP,
        ),
    )


def _dispatch_schema_fixture(con: sqlite3.Connection, material: str) -> None:
    """Schema-only uniqueness fixture; production dispatch remains API-108 owned."""
    m = con.execute(
        "SELECT action_id,business_key,material_sha256,operation_class,episode_id "
        "FROM material_actions WHERE material_action_id=?",
        (material,),
    ).fetchone()
    db_uuid = con.execute("SELECT identity_uuid FROM database_identity").fetchone()[0]
    con.execute(
        "INSERT INTO original_actions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            m[0],
            material,
            m[1],
            m[2],
            m[3],
            m[4],
            EPOCH,
            db_uuid,
            "DISPATCHED",
            STAMP,
            "schema-test-writer",
            STAMP,
            None,
        ),
    )


def test_queue_dual_unique_constraints_only(seeded: Path) -> None:
    con = open_existing(seeded)
    with immediate(con):
        _episode(con)
        _material(con)
        _material(con, material="material-2", action="action-2")
        _dispatch_schema_fixture(con, "material-1")
        _dispatch_schema_fixture(con, "material-2")
        con.execute(
            "INSERT INTO queue_effects VALUES (?,?,?,?,?,?,?,?)",
            (
                "effect-1",
                "action-1",
                business_key(
                    org_id="org_demo",
                    interaction_id="BI-01",
                    operation="ROUTINE_ORDER_STATUS",
                ),
                "material-1",
                "contact-c1",
                "a" * 64,
                "Synthetic status",
                STAMP,
            ),
        )
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "INSERT INTO queue_effects VALUES (?,?,?,?,?,?,?,?)",
            (
                "effect-2",
                "action-2",
                business_key(
                    org_id="org_demo",
                    interaction_id="BI-01",
                    operation="ROUTINE_ORDER_STATUS",
                ),
                "material-2",
                "contact-c1",
                "a" * 64,
                "Synthetic status",
                STAMP,
            ),
        )
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "INSERT INTO queue_effects VALUES (?,?,?,?,?,?,?,?)",
            (
                "effect-3",
                "action-1",
                "b" * 64,
                "material-1",
                "contact-c1",
                "a" * 64,
                "Synthetic status",
                STAMP,
            ),
        )
    assert con.execute("SELECT count(*) FROM queue_effects").fetchone() == (1,)
    con.close()


def test_b_proposal_branch_and_invocation_fk(seeded: Path) -> None:
    con = open_existing(seeded)
    with immediate(con):
        _episode(con)
        con.execute(
            "INSERT INTO agent_invocations VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                "inv-b",
                "ep-1",
                "B",
                "test",
                "none",
                0,
                "COMPLETE",
                STAMP,
                STAMP,
                None,
                "[]",
            ),
        )
        con.execute(
            "INSERT INTO handoffs VALUES (?,?,?,?,?,?,?,?)",
            ("handoff-1", "ep-1", "{}", "{}", "{}", "{}", STAMP, "VALID"),
        )
        con.execute(
            "INSERT INTO verifications VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                "verify-1",
                "ep-1",
                "grant-01",
                "T-001",
                "C1",
                "A1",
                "ORD-101",
                "contact-c1",
                '{"order_version":1,"recipient_version":1}',
                "VERIFIED",
                STAMP,
            ),
        )
        con.execute(
            "INSERT INTO tool_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "tool-1",
                "inv-b",
                "call-1",
                "read_verified_order_status",
                "{}",
                "{}",
                "TRUSTED_ORDER_REPOSITORY",
                STAMP,
                "SUCCESS",
                "handoff-1",
                "verify-1",
                1,
                2,
            ),
        )
        con.execute(
            "INSERT INTO agent_b_proposals VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                "proposal-1",
                "ep-1",
                "inv-b",
                "handoff-1",
                "STATUS_BRIEF",
                "STATUS_T1",
                '["order_id","order_status"]',
                "tool-1",
                None,
                "PROPOSED",
                3,
            ),
        )
        con.execute(
            "INSERT INTO agent_b_proposals VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                "proposal-2",
                "ep-1",
                "inv-b",
                "handoff-1",
                None,
                None,
                None,
                None,
                "unable",
                "UNABLE",
                4,
            ),
        )
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "INSERT INTO agent_b_proposals VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                "bad",
                "ep-1",
                "inv-b",
                "handoff-1",
                "STATUS_BRIEF",
                "STATUS_T1",
                "[]",
                "missing",
                None,
                "PROPOSED",
                5,
            ),
        )
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "INSERT INTO agent_b_proposals VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                "bad2",
                "ep-1",
                "inv-b",
                "handoff-1",
                None,
                None,
                None,
                None,
                None,
                "PROPOSED",
                5,
            ),
        )
    assert con.execute("SELECT count(*) FROM agent_b_proposals").fetchone() == (2,)
    con.close()


def test_approval_pending_and_invalid_actor(seeded: Path) -> None:
    con = open_existing(seeded)
    with immediate(con):
        _episode(con)
        _material(con, operation="SANITIZED_INTERNAL_ESCALATION")
        con.execute(
            "INSERT INTO escalation_approvals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "approval-1",
                "material-1",
                "a" * 64,
                None,
                None,
                None,
                None,
                "PENDING",
                None,
                "2030-01-01T00:00:00.000000Z",
                None,
                None,
                1,
            ),
        )
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "INSERT INTO escalation_approvals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "approval-2",
                "material-1",
                "a" * 64,
                "supervisor-1",
                1,
                "APPROVE",
                STAMP,
                "PENDING",
                None,
                "2030-01-01T00:00:00.000000Z",
                None,
                None,
                1,
            ),
        )
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "UPDATE escalation_approvals SET status='APPROVED' WHERE approval_id='approval-1'"
        )
    con.execute(
        "UPDATE escalation_approvals SET status='APPROVED',supervisor_id='supervisor-1',supervisor_version=1,decision='APPROVE',decided_at=?,approved_at=?,version=2 WHERE approval_id='approval-1'",
        (STAMP, STAMP),
    )
    assert con.execute(
        "SELECT status,supervisor_id FROM escalation_approvals"
    ).fetchone() == ("APPROVED", "supervisor-1")
    check_integrity(con)
    con.close()


def test_cross_org_and_interaction_relationships(seeded: Path) -> None:
    con = open_existing(seeded)
    con.execute(
        "INSERT INTO organizations VALUES (?,?,?,?,?)",
        ("other", "Other synthetic org", 1, 1, 1),
    )
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "INSERT INTO accounts VALUES (?,?,?,?,?)",
            ("bad-account", "other", "C1", 1, 1),
        )
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "INSERT INTO orders VALUES (?,?,?,?,?,?,?,?)",
            ("bad-order", "other", "A1", "PLACED", None, None, 1, 1),
        )
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "INSERT INTO ticket_sources VALUES (?,?,?,?,?,?,?)",
            ("bad-source", "T-002", 2, "Other text", "a" * 64, "BI-01", STAMP),
        )
    check_integrity(con)
    con.close()


def test_b_wrong_role_and_status_read_projection(seeded: Path) -> None:
    con = open_existing(seeded)
    with immediate(con):
        _episode(con)
        con.execute(
            "INSERT INTO agent_invocations VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                "inv-a",
                "ep-1",
                "A",
                "test",
                "none",
                0,
                "COMPLETE",
                STAMP,
                STAMP,
                None,
                "[]",
            ),
        )
        con.execute(
            "INSERT INTO handoffs VALUES (?,?,?,?,?,?,?,?)",
            (
                "h-a",
                "ep-1",
                '{"grant_id":"grant-01","grant_version":1,"assignment_version":1}',
                '{"verification_id":"verify-a"}',
                "{}",
                "{}",
                STAMP,
                "VALID",
            ),
        )
        con.execute(
            "INSERT INTO verifications VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                "verify-a",
                "ep-1",
                "grant-01",
                "T-001",
                "C1",
                "A1",
                "ORD-101",
                "contact-c1",
                '{"order_version":1,"recipient_version":1}',
                "VERIFIED",
                STAMP,
            ),
        )
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "INSERT INTO agent_b_proposals VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                "bad-role",
                "ep-1",
                "inv-a",
                "h-a",
                None,
                None,
                None,
                None,
                None,
                "UNABLE",
                1,
            ),
        )
    view = AgentReadRepository(con).verified_order_status(
        verification_id="verify-a", episode_id="ep-1", handoff_id="h-a"
    )
    assert view.order_id == "ORD-101" and view.customer_visible_eta == "2026-10-20"
    assert not hasattr(view, "internal_notes") and not hasattr(view, "expected_branch")
    with pytest.raises(ScopeError):
        AgentReadRepository(con).verified_order_status(
            verification_id="verify-a", episode_id="wrong", handoff_id="h-a"
        )
    con.close()


def test_missing_schema_table_detected(tmp_path: Path) -> None:
    path = tmp_path / "missing.db"
    initialize(path)
    con = open_existing(path)
    con.execute("DROP TABLE evaluation_results")
    con.close()
    with pytest.raises(DatabaseError, match="missing schema tables"):
        initialize(path)


def test_seed_failure_rolls_back(tmp_path: Path) -> None:
    path = tmp_path / "bad-seed.db"
    initialize(path)
    malformed = tmp_path / "bad-sources.json"
    rows = json.loads(SOURCES.read_text(encoding="utf-8"))
    rows["sources"][1]["ticket_id"] = rows["sources"][0]["ticket_id"]
    malformed.write_text(json.dumps(rows), encoding="utf-8")
    with pytest.raises(sqlite3.IntegrityError):
        seed_synthetic(path, malformed, ORACLE)
    con = open_existing(path)
    assert con.execute("SELECT count(*) FROM organizations").fetchone() == (0,)
    assert con.execute("SELECT count(*) FROM scenarios").fetchone() == (0,)
    check_integrity(con)
    con.close()


def test_effect_material_identity_is_relationally_bound(seeded: Path) -> None:
    con = open_existing(seeded)
    _episode(con)
    _material(con)
    key = business_key(
        org_id="org_demo", interaction_id="BI-01", operation="ROUTINE_ORDER_STATUS"
    )
    for action, digest, recipient, body in [
        ("different-action", "a" * 64, "contact-c1", "Synthetic status"),
        ("action-1", "b" * 64, "contact-c1", "Synthetic status"),
        ("action-1", "a" * 64, "contact-c2", "Synthetic status"),
        ("action-1", "a" * 64, "contact-c1", "Changed body"),
    ]:
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(
                "INSERT INTO queue_effects VALUES (?,?,?,?,?,?,?,?)",
                (
                    "effect-bad",
                    action,
                    key,
                    "material-1",
                    recipient,
                    digest,
                    body,
                    STAMP,
                ),
            )
    assert con.execute("SELECT count(*) FROM queue_effects").fetchone() == (0,)
    con.close()


def test_approval_digest_and_one_active_preview(seeded: Path) -> None:
    con = open_existing(seeded)
    _episode(con)
    _material(con, operation="SANITIZED_INTERNAL_ESCALATION")
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "INSERT INTO escalation_approvals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "bad-digest",
                "material-1",
                "b" * 64,
                None,
                None,
                None,
                None,
                "PENDING",
                None,
                "2030-01-01T00:00:00Z",
                None,
                None,
                1,
            ),
        )
    con.execute(
        "INSERT INTO escalation_approvals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "pending",
            "material-1",
            "a" * 64,
            None,
            None,
            None,
            None,
            "PENDING",
            None,
            "2030-01-01T00:00:00Z",
            None,
            None,
            1,
        ),
    )
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "INSERT INTO escalation_approvals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "second",
                "material-1",
                "a" * 64,
                None,
                None,
                None,
                None,
                "PENDING",
                None,
                "2030-01-01T00:00:00Z",
                None,
                None,
                1,
            ),
        )
    with pytest.raises(sqlite3.IntegrityError):
        with immediate(con):
            con.execute(
                "UPDATE escalation_approvals SET status='APPROVED' WHERE approval_id='pending'"
            )
    assert con.execute(
        "SELECT status FROM escalation_approvals WHERE approval_id='pending'"
    ).fetchone() == ("PENDING",)
    check_integrity(con)
    con.close()


def test_b_observation_requires_success_and_same_episode(seeded: Path) -> None:
    con = open_existing(seeded)
    _episode(con)
    con.execute(
        "INSERT INTO agent_invocations VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("inv-b", "ep-1", "B", "test", "none", 0, "COMPLETE", STAMP, STAMP, None, "[]"),
    )
    con.execute(
        "INSERT INTO handoffs VALUES (?,?,?,?,?,?,?,?)",
        ("handoff-1", "ep-1", "{}", "{}", "{}", "{}", STAMP, "VALID"),
    )
    con.execute(
        "INSERT INTO verifications VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            "verify-1",
            "ep-1",
            "grant-01",
            "T-001",
            "C1",
            "A1",
            "ORD-101",
            "contact-c1",
            "{}",
            "VERIFIED",
            STAMP,
        ),
    )
    con.execute(
        "INSERT INTO tool_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "failed-tool",
            "inv-b",
            "call-1",
            "read_verified_order_status",
            "{}",
            "{}",
            "TRUSTED_ORDER_REPOSITORY",
            STAMP,
            "ERROR",
            "handoff-1",
            "verify-1",
            1,
            2,
        ),
    )
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "INSERT INTO agent_b_proposals VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                "bad",
                "ep-1",
                "inv-b",
                "handoff-1",
                "STATUS_BRIEF",
                "STATUS_T1",
                "[]",
                "failed-tool",
                None,
                "PROPOSED",
                3,
            ),
        )
    con.execute(
        "INSERT INTO agent_b_proposals VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            "refusal",
            "ep-1",
            "inv-b",
            "handoff-1",
            None,
            None,
            None,
            None,
            "No supported observation",
            "REFUSED",
            3,
        ),
    )
    con.close()


def test_new_interaction_is_distinct_business_key(seeded: Path) -> None:
    con = open_existing(seeded)
    _episode(con)
    first = business_key(
        org_id="org_demo", interaction_id="BI-01", operation="ROUTINE_ORDER_STATUS"
    )
    second = business_key(
        org_id="org_demo", interaction_id="BI-NEW", operation="ROUTINE_ORDER_STATUS"
    )
    _material(con, material="first", action="action-1", key=first)
    _material(con, material="second", action="action-2", key=second)
    _dispatch_schema_fixture(con, "first")
    _dispatch_schema_fixture(con, "second")
    con.execute(
        "INSERT INTO queue_effects VALUES (?,?,?,?,?,?,?,?)",
        (
            "effect-1",
            "action-1",
            first,
            "first",
            "contact-c1",
            "a" * 64,
            "Synthetic status",
            STAMP,
        ),
    )
    con.execute(
        "INSERT INTO queue_effects VALUES (?,?,?,?,?,?,?,?)",
        (
            "effect-2",
            "action-2",
            second,
            "second",
            "contact-c1",
            "a" * 64,
            "Synthetic status",
            STAMP,
        ),
    )
    assert con.execute("SELECT count(*) FROM queue_effects").fetchone() == (2,)
    check_integrity(con)
    con.close()


def test_interrupted_ddl_transaction_reopens_cleanly(tmp_path: Path) -> None:
    path = tmp_path / "interrupted.db"
    initialize(path)
    con = open_existing(path)
    with pytest.raises(RuntimeError, match="simulated interruption"):
        with immediate(con):
            con.execute("CREATE TABLE interrupted_marker (id INTEGER PRIMARY KEY)")
            raise RuntimeError("simulated interruption")
    con.close()
    reopened = open_existing(path)
    assert (
        reopened.execute(
            "SELECT name FROM sqlite_master WHERE name='interrupted_marker'"
        ).fetchone()
        is None
    )
    check_integrity(reopened)
    reopened.close()
    initialize(path)


def test_b_cross_episode_and_invocation_rejected(seeded: Path) -> None:
    con = open_existing(seeded)
    _episode(con)
    _episode(con, episode="ep-2")
    for invocation, episode in (("inv-1", "ep-1"), ("inv-2", "ep-2")):
        con.execute(
            "INSERT INTO agent_invocations VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                invocation,
                episode,
                "B",
                "test",
                "none",
                0,
                "COMPLETE",
                STAMP,
                STAMP,
                None,
                "[]",
            ),
        )
    con.execute(
        "INSERT INTO handoffs VALUES (?,?,?,?,?,?,?,?)",
        ("handoff-1", "ep-1", "{}", "{}", "{}", "{}", STAMP, "VALID"),
    )
    con.execute(
        "INSERT INTO verifications VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            "verify-1",
            "ep-1",
            "grant-01",
            "T-001",
            "C1",
            "A1",
            "ORD-101",
            "contact-c1",
            "{}",
            "VERIFIED",
            STAMP,
        ),
    )
    con.execute(
        "INSERT INTO tool_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "tool-1",
            "inv-1",
            "call-1",
            "read_verified_order_status",
            "{}",
            "{}",
            "TRUSTED_ORDER_REPOSITORY",
            STAMP,
            "SUCCESS",
            "handoff-1",
            "verify-1",
            1,
            2,
        ),
    )
    for episode, invocation in (("ep-2", "inv-1"), ("ep-1", "inv-2")):
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(
                "INSERT INTO agent_b_proposals VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "bad-proposal",
                    episode,
                    invocation,
                    "handoff-1",
                    "STATUS_BRIEF",
                    "STATUS_T1",
                    "[]",
                    "tool-1",
                    None,
                    "PROPOSED",
                    3,
                ),
            )
    check_integrity(con)
    con.close()
