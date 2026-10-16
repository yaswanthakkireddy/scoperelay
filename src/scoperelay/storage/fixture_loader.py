"""Explicit trusted bootstrap for one disposable synthetic fixture epoch.

No reset or effect-writing API is provided. Ticket text is inserted only into
ticket_sources; all authority records are fixed trusted data below.
"""

from hashlib import sha256
import json
from pathlib import Path
import unicodedata

from .database import DatabaseError, check_integrity, open_existing
from .schema import SCHEMA_VERSION
from .transaction import immediate
from .source_spans import source_digest


STAMP = "2026-10-14T00:00:00.000000Z"
EPOCH = "synthetic-epoch-1"
ORG = "org_demo"


def business_key(
    *, org_id: str, interaction_id: str, operation: str, epoch: str = EPOCH
) -> str:
    """Task 03A V1 identity; caller must supply independently trusted IDs."""
    if not org_id or not interaction_id or not epoch:
        raise ValueError("empty trusted identity")
    if operation not in {"ROUTINE_ORDER_STATUS", "SANITIZED_INTERNAL_ESCALATION"}:
        raise ValueError("unsupported operation")
    payload = dict(
        schema_version=1,
        fixture_epoch=unicodedata.normalize("NFC", epoch),
        org_id=unicodedata.normalize("NFC", org_id),
        business_interaction_id=unicodedata.normalize("NFC", interaction_id),
        operation_class=operation,
        request_ordinal=1,
    )
    encoded = json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def seed_synthetic(path: Path, source_file: Path, oracle_file: Path | None) -> bool:
    """Seed a compatible empty DB once; return False on an identical repeat.

    Existing effects, a different epoch, or any changed source/authority row
    make this fail closed. Call schema.initialize(path) separately first.
    """
    sources_doc = json.loads(source_file.read_text(encoding="utf-8"))
    sources = sources_doc["sources"]
    labels = (
        {
            r["scenario_id"]: r
            for r in json.loads(oracle_file.read_text(encoding="utf-8"))["labels"]
        }
        if oracle_file is not None
        else {}
    )
    if sources_doc["fixture_version"] != "task03-source-draft-v1":
        raise DatabaseError("unsupported source fixture version")
    source_ids = [r["scenario_id"] for r in sources]
    if (
        len(sources) != 8
        or len(set(source_ids)) != 8
        or (oracle_file is not None and set(source_ids) != set(labels))
    ):
        raise DatabaseError("scenario/oracle mismatch")
    con = open_existing(path)
    try:
        if con.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
            raise DatabaseError("incompatible schema version")
        with immediate(con):
            existing = con.execute(
                "SELECT scenario_id,fixture_version,fixture_epoch FROM scenarios"
            ).fetchall()
            if existing:
                expected = [
                    (r["scenario_id"], sources_doc["fixture_version"], EPOCH)
                    for r in sources
                ]
                if sorted(existing) != sorted(expected):
                    raise DatabaseError("incompatible fixture identity")
                for row in sources:
                    actual = con.execute(
                        "SELECT t.current_source_version,s.body_text,s.body_sha256,s.business_interaction_id "
                        "FROM tickets t JOIN ticket_sources s ON s.ticket_id=t.ticket_id "
                        "WHERE t.ticket_id=? AND s.source_version=?",
                        (row["ticket_id"], row["source_version"]),
                    ).fetchone()
                    if actual != (
                        1,
                        row["body_text"],
                        source_digest(row["body_text"]),
                        f"BI-{row['scenario_id'][-2:]}",
                    ):
                        raise DatabaseError("fixture source was modified")
                if any(
                    con.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone()
                    for table in (
                        "queue_effects",
                        "resource_attempts",
                        "journal_events",
                    )
                ):
                    raise DatabaseError("fixture has protected effect history")
                return False
            if any(
                con.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone()
                for table in (
                    "organizations",
                    "tickets",
                    "ticket_sources",
                    "evaluation_truth",
                    "queue_effects",
                    "resource_attempts",
                    "journal_events",
                )
            ):
                raise DatabaseError("database is not an empty fixture target")
            con.execute(
                "INSERT INTO organizations VALUES (?,?,?,?,?)",
                (ORG, "ScopeRelay Synthetic Support", 1, 1, 1),
            )
            con.executemany(
                "INSERT INTO principals VALUES (?,?,?,?,?,?)",
                [
                    ("manager-1", ORG, "MANAGER", "Synthetic Manager", 1, 1),
                    ("supervisor-1", ORG, "SUPERVISOR", "Synthetic Supervisor", 1, 1),
                    ("evaluator-1", ORG, "EVALUATOR", "Synthetic Evaluator", 1, 1),
                ],
            )
            con.executemany(
                "INSERT INTO customers VALUES (?,?,?,?,?)",
                [
                    ("C1", ORG, "Synthetic Customer One", 1, 1),
                    ("C2", ORG, "Synthetic Customer Two", 1, 1),
                ],
            )
            con.executemany(
                "INSERT INTO accounts VALUES (?,?,?,?,?)",
                [
                    ("A1", ORG, "C1", 1, 1),
                    ("A2", ORG, "C2", 1, 1),
                ],
            )
            con.executemany(
                "INSERT INTO contacts VALUES (?,?,?,?,?,?,?,?,?)",
                [
                    (
                        "contact-c1",
                        ORG,
                        "C1",
                        None,
                        "SYNTHETIC_CUSTOMER_QUEUE",
                        "c1@example.invalid",
                        1,
                        1,
                        1,
                    ),
                    (
                        "contact-c2",
                        ORG,
                        "C2",
                        None,
                        "SYNTHETIC_CUSTOMER_QUEUE",
                        "c2@example.invalid",
                        1,
                        1,
                        1,
                    ),
                    (
                        "contact-inactive",
                        ORG,
                        "C1",
                        None,
                        "SYNTHETIC_CUSTOMER_QUEUE",
                        "old@example.invalid",
                        0,
                        0,
                        1,
                    ),
                    (
                        "internal-support",
                        ORG,
                        None,
                        "supervisor-1",
                        "SYNTHETIC_INTERNAL_QUEUE",
                        "internal@example.invalid",
                        1,
                        1,
                        1,
                    ),
                ],
            )
            con.execute(
                "INSERT INTO org_policy VALUES (?,?,?,?,?,?,?,?)",
                (
                    ORG,
                    1,
                    _json(["ROUTINE_ORDER_STATUS", "SANITIZED_INTERNAL_ESCALATION"]),
                    _json(["order_id", "order_status", "customer_visible_eta"]),
                    _json(["ticket_id", "order_id", "reason_category", "summary_code"]),
                    _json(["STATUS_CONFLICT", "MANUAL_REVIEW_REQUIRED"]),
                    "internal-support",
                    STAMP,
                ),
            )
            con.executemany(
                "INSERT INTO orders VALUES (?,?,?,?,?,?,?,?)",
                [
                    (
                        "ORD-101",
                        ORG,
                        "A1",
                        "IN_TRANSIT",
                        "2026-10-20",
                        "Synthetic confidential routing note",
                        1,
                        1,
                    ),
                    ("ORD-102", ORG, "A1", "PROCESSING", None, None, 1, 1),
                    (
                        "ORD-201",
                        ORG,
                        "A2",
                        "PLACED",
                        None,
                        "Other account's private note",
                        1,
                        1,
                    ),
                ],
            )
            for row in sources:
                scenario = row["scenario_id"]
                ticket = row["ticket_id"]
                customer = "C2" if scenario == "SC-05" else "C1"
                operation = (
                    "SANITIZED_INTERNAL_ESCALATION"
                    if scenario == "SC-06"
                    else "ROUTINE_ORDER_STATUS"
                )
                grant_id = f"grant-{scenario[-2:]}"
                con.execute(
                    "INSERT INTO tickets VALUES (?,?,?,?,?,?,?)",
                    (ticket, ORG, customer, None, 1, 1, 1),
                )
                con.execute(
                    "INSERT INTO manager_grants VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        grant_id,
                        ORG,
                        "manager-1",
                        operation,
                        ticket,
                        1,
                        1,
                        "2030-01-01T00:00:00.000000Z",
                        None,
                        STAMP,
                    ),
                )
                con.execute(
                    "UPDATE tickets SET assigned_grant_id=? WHERE ticket_id=?",
                    (grant_id, ticket),
                )
                body = row["body_text"]
                con.execute(
                    "INSERT INTO ticket_sources VALUES (?,?,?,?,?,?,?)",
                    (
                        f"source-{scenario[-2:]}",
                        ticket,
                        row["source_version"],
                        body,
                        source_digest(body),
                        f"BI-{scenario[-2:]}",
                        STAMP,
                    ),
                )
                con.execute(
                    "INSERT INTO scenarios VALUES (?,?,?,?,?,?)",
                    (
                        scenario,
                        sources_doc["fixture_version"],
                        EPOCH,
                        f"Synthetic {scenario}",
                        _json({"ticket_id": ticket, "source_version": 1}),
                        STAMP,
                    ),
                )
                if oracle_file is not None:
                    label = labels[scenario]
                    con.execute(
                        "INSERT INTO evaluation_truth VALUES (?,?,?,?,?,?)",
                        (
                            f"truth-{scenario[-2:]}",
                            scenario,
                            "v1",
                            _json(
                                {
                                    "expected_branch": label["expected_branch"],
                                    "requires_approval": label.get(
                                        "requires_approval", False
                                    ),
                                }
                            ),
                            _json(label["allowed_effects"]),
                            None,
                        ),
                    )
            con.execute(
                "INSERT INTO manager_grants VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    "grant-revoked",
                    ORG,
                    "manager-1",
                    "ROUTINE_ORDER_STATUS",
                    "T-001",
                    1,
                    2,
                    "2030-01-01T00:00:00.000000Z",
                    STAMP,
                    STAMP,
                ),
            )
        check_integrity(con)
        return True
    finally:
        con.close()
