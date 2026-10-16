"""Preliminary POLICY/GRANT + authoritative business verification (API-102/103).

This is not API-108: no queue write or durable effect authorization occurs here.
The protected resource must repeat current-state checks in its own writer txn.
"""

from datetime import datetime, timezone
import json
import re
import sqlite3

from scoperelay.storage.source_spans import SourceReferenceError, resolve_span

from .contracts import (
    CandidateRequestV1,
    EligibilityResult,
    TrustedStateError,
    TrustedTask,
    VerifiedFacts,
)


OPS = frozenset({"ROUTINE_ORDER_STATUS", "SANITIZED_INTERNAL_ESCALATION"})
ROUTINE_FIELDS = frozenset({"order_id", "order_status", "customer_visible_eta"})
INTERNAL_FIELDS = frozenset(
    {"ticket_id", "order_id", "reason_category", "summary_code"}
)
REASONS = frozenset({"STATUS_CONFLICT", "MANUAL_REVIEW_REQUIRED"})
FORBIDDEN_CONTENT = frozenset(
    {"INTERNAL_NOTES", "HISTORY_EXPORT", "FULL_ACCOUNT_HISTORY", "EXPORT", "ATTACHMENT"}
)


def _utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise TrustedStateError("malformed trusted UTC timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise TrustedStateError("trusted timestamp must be UTC")
    return parsed


def _catalog(raw: str, allowed: frozenset[str]) -> frozenset[str]:
    try:
        value = json.loads(raw)
    except ValueError as exc:
        raise TrustedStateError("corrupt policy catalog") from exc
    if (
        not isinstance(value, list)
        or not value
        or any(type(item) is not str for item in value)
    ):
        raise TrustedStateError("invalid policy catalog shape")
    result = frozenset(value)
    if len(result) != len(value) or not result <= allowed:
        raise TrustedStateError("policy exceeds compiled catalog")
    return result


def evaluate(
    con: sqlite3.Connection,
    task: TrustedTask,
    candidate: CandidateRequestV1,
    *,
    at: datetime,
) -> EligibilityResult:
    """Use server-owned episode/task and DB facts; candidate only selects a hypothesis.

    Execution/DB/corrupt-state exceptions propagate and never become business ALLOW.
    A returned decision is preliminary and deliberately creates no resource attempt.
    """
    if at.tzinfo is None or at.utcoffset() != timezone.utc.utcoffset(at):
        raise ValueError("evaluation clock must be UTC")
    if type(task.assignment_version) is not int or task.assignment_version < 1:
        raise ValueError("invalid trusted assignment version")
    previous_factory = con.row_factory
    con.row_factory = sqlite3.Row
    try:
        return _evaluate(con, task, candidate, at)
    finally:
        con.row_factory = previous_factory


def _evaluate(
    con: sqlite3.Connection,
    task: TrustedTask,
    candidate: CandidateRequestV1,
    at: datetime,
) -> EligibilityResult:
    episode = con.execute(
        "SELECT e.episode_id,e.fixture_epoch,e.ticket_id,e.source_version,e.grant_id,"
        "e.scenario_id,t.org_id,t.customer_id,t.assigned_grant_id,t.assignment_version,"
        "t.current_source_version,t.active AS ticket_active,o.active AS org_active,"
        "o.policy_version AS org_policy_version "
        "FROM episodes e JOIN tickets t ON t.ticket_id=e.ticket_id "
        "JOIN organizations o ON o.org_id=t.org_id WHERE e.episode_id=?",
        (task.episode_id,),
    ).fetchone()
    if episode is None:
        return EligibilityResult("INCOMPLETE EVIDENCE", "UNKNOWN_EPISODE")
    if candidate.ticket_id != episode["ticket_id"]:
        return EligibilityResult("DENY", "WRONG_TICKET")
    if not episode["org_active"] or not episode["ticket_active"]:
        return EligibilityResult("DENY", "INACTIVE_ORG_OR_TICKET")
    if (
        episode["assigned_grant_id"] != episode["grant_id"]
        or episode["assignment_version"] != task.assignment_version
        or episode["current_source_version"] != episode["source_version"]
    ):
        return EligibilityResult("DENY", "STALE_ASSIGNMENT_OR_SOURCE")
    scenario = con.execute(
        "SELECT fixture_epoch FROM scenarios WHERE scenario_id=?",
        (episode["scenario_id"],),
    ).fetchone()
    if scenario is None or scenario["fixture_epoch"] != episode["fixture_epoch"]:
        raise TrustedStateError("episode fixture epoch mismatch")
    source = con.execute(
        "SELECT source_id,body_text,body_sha256,business_interaction_id FROM ticket_sources "
        "WHERE ticket_id=? AND source_version=?",
        (episode["ticket_id"], episode["source_version"]),
    ).fetchone()
    if source is None:
        return EligibilityResult("INCOMPLETE EVIDENCE", "SOURCE_MISSING")
    for span in candidate.source_spans:
        if span.source_id != source["source_id"] or not span.excerpt:
            raise SourceReferenceError("source ID or nonempty excerpt mismatch")
        resolve_span(
            expected_ticket_id=episode["ticket_id"],
            expected_source_version=episode["source_version"],
            ticket_id=candidate.ticket_id,
            source_version=span.source_version,
            body_text=source["body_text"],
            body_sha256=source["body_sha256"],
            start=span.start,
            end=span.end,
            excerpt=span.excerpt,
        )
    # Closed negative indicators are conservative blockers, not a general intent parser.
    # In particular, a model's STATUS_ONLY paraphrase cannot erase explicit restricted
    # data requests present in the actual immutable source (SC-02/03/05).
    lowered = source["body_text"].casefold()
    if any(
        term in lowered
        for term in ("internal notes", "account history", "order history")
    ):
        return EligibilityResult("DENY", "SOURCE_REQUESTS_FORBIDDEN_DATA")
    policy = con.execute(
        "SELECT p.*,o.policy_version AS org_version FROM org_policy p "
        "JOIN organizations o ON o.org_id=p.org_id WHERE p.org_id=?",
        (episode["org_id"],),
    ).fetchone()
    if policy is None:
        return EligibilityResult("INCOMPLETE EVIDENCE", "POLICY_MISSING")
    permitted = _catalog(policy["permitted_ops_json"], OPS)
    _catalog(policy["routine_field_allowlist_json"], ROUTINE_FIELDS)
    _catalog(policy["internal_field_allowlist_json"], INTERNAL_FIELDS)
    reasons = _catalog(policy["escalation_reasons_json"], REASONS)
    if policy["version"] != episode["org_policy_version"]:
        return EligibilityResult("DENY", "POLICY_VERSION_CHANGED")
    grant = con.execute(
        "SELECT g.*,p.role AS manager_role,p.active AS manager_active,p.version AS manager_version "
        "FROM manager_grants g JOIN principals p ON p.principal_id=g.manager_id "
        "WHERE g.grant_id=?",
        (episode["grant_id"],),
    ).fetchone()
    if grant is None:
        return EligibilityResult("INCOMPLETE EVIDENCE", "GRANT_MISSING")
    if (
        grant["org_id"] != episode["org_id"]
        or grant["scope_ticket_id"] != episode["ticket_id"]
        or grant["manager_role"] != "MANAGER"
        or not grant["manager_active"]
    ):
        return EligibilityResult("DENY", "GRANT_SCOPE_OR_ISSUER_INVALID")
    if grant["revoked_at"] is not None or _utc(grant["expires_at"]) <= at:
        return EligibilityResult("DENY", "GRANT_REVOKED_OR_EXPIRED")
    if grant["policy_version"] != policy["version"]:
        return EligibilityResult("DENY", "GRANT_POLICY_STALE")
    if (
        candidate.operation_hint == "UNSUPPORTED_REQUEST"
        or candidate.content_class_hint in FORBIDDEN_CONTENT
    ):
        return EligibilityResult("DENY", "UNSUPPORTED_OR_FORBIDDEN_REQUEST")
    if candidate.operation_hint in OPS and (
        grant["operation_class"] != candidate.operation_hint
        or candidate.operation_hint not in permitted
    ):
        return EligibilityResult("DENY", "OPERATION_NOT_GRANTED")
    source_orders = set(re.findall(r"\bORD-[0-9]+\b", source["body_text"]))
    if (
        candidate.ambiguous
        or candidate.conflicting_references
        or candidate.quoted_only
        or candidate.operation_hint == "UNCERTAIN"
        or len(source_orders) > 1
    ):
        return EligibilityResult(
            "CLARIFY",
            "INTENT_AMBIGUOUS",
            clarification_draft="Please clarify which order or request you mean.",
        )
    if candidate.operation_hint == "ROUTINE_ORDER_STATUS":
        return _routine(con, task, candidate, episode, source, grant, policy)
    if candidate.operation_hint == "SANITIZED_INTERNAL_ESCALATION":
        return _internal(con, task, candidate, episode, source, grant, policy, reasons)
    raise TrustedStateError("unhandled extraction label")


def _facts(
    *,
    episode: sqlite3.Row,
    source: sqlite3.Row,
    grant: sqlite3.Row,
    policy: sqlite3.Row,
    operation: str,
    account_id: str | None,
    order_id: str | None,
    order_version: int | None,
    order_status: str | None,
    eta: str | None,
    recipient: sqlite3.Row,
    reason: str | None,
) -> VerifiedFacts:
    return VerifiedFacts(
        episode["episode_id"],
        episode["fixture_epoch"],
        episode["org_id"],
        policy["version"],
        grant["grant_id"],
        grant["version"],
        episode["ticket_id"],
        source["source_id"],
        episode["source_version"],
        source["body_sha256"],
        episode["assignment_version"],
        source["business_interaction_id"],
        operation,
        episode["customer_id"],
        account_id,
        order_id,
        order_version,
        order_status,
        eta,
        recipient["contact_id"],
        recipient["version"],
        recipient["channel"],
        reason,
    )


def _routine(
    con: sqlite3.Connection,
    task: TrustedTask,
    candidate: CandidateRequestV1,
    episode: sqlite3.Row,
    source: sqlite3.Row,
    grant: sqlite3.Row,
    policy: sqlite3.Row,
) -> EligibilityResult:
    if not candidate.order_ref_text:
        return EligibilityResult("INCOMPLETE EVIDENCE", "ORDER_REFERENCE_MISSING")
    order = con.execute(
        "SELECT o.order_id,o.org_id,o.account_id,o.order_status,o.customer_visible_eta,o.version,"
        "o.active,a.customer_id,a.org_id AS account_org,a.active AS account_active,"
        "c.active AS customer_active FROM orders o JOIN accounts a ON a.account_id=o.account_id "
        "JOIN customers c ON c.customer_id=a.customer_id WHERE o.order_id=?",
        (candidate.order_ref_text,),
    ).fetchone()
    if order is None:
        return EligibilityResult("INCOMPLETE EVIDENCE", "ORDER_NOT_ESTABLISHED")
    if (
        order["org_id"] != episode["org_id"]
        or order["account_org"] != episode["org_id"]
        or order["customer_id"] != episode["customer_id"]
    ):
        return EligibilityResult("DENY", "WRONG_OWNER_OR_ORG")
    if not any(
        candidate.order_ref_text in span.excerpt for span in candidate.source_spans
    ):
        return EligibilityResult("INCOMPLETE EVIDENCE", "ORDER_REFERENCE_NOT_SOURCED")
    if (
        not order["active"]
        or not order["account_active"]
        or not order["customer_active"]
    ):
        return EligibilityResult("INCOMPLETE EVIDENCE", "BUSINESS_RECORD_INACTIVE")
    if order["order_status"] not in {
        "PLACED",
        "PROCESSING",
        "IN_TRANSIT",
        "DELIVERED",
        "DELAYED",
    }:
        raise TrustedStateError("invalid trusted order status")
    contacts = con.execute(
        "SELECT * FROM contacts WHERE org_id=? AND customer_id=? AND channel='SYNTHETIC_CUSTOMER_QUEUE' "
        "AND active=1 AND verified=1 ORDER BY contact_id",
        (episode["org_id"], episode["customer_id"]),
    ).fetchall()
    if len(contacts) != 1:
        return EligibilityResult("INCOMPLETE EVIDENCE", "VERIFIED_CONTACT_NOT_UNIQUE")
    recipient = contacts[0]
    if (
        candidate.requested_recipient_text is not None
        and candidate.requested_recipient_text != recipient["address"]
    ):
        return EligibilityResult("DENY", "WRONG_RECIPIENT_REQUESTED")
    facts = _facts(
        episode=episode,
        source=source,
        grant=grant,
        policy=policy,
        operation="ROUTINE_ORDER_STATUS",
        account_id=order["account_id"],
        order_id=order["order_id"],
        order_version=order["version"],
        order_status=order["order_status"],
        eta=order["customer_visible_eta"],
        recipient=recipient,
        reason=None,
    )
    return EligibilityResult("ALLOW", "ROUTINE_VERIFIED", facts)


def _internal(
    con: sqlite3.Connection,
    task: TrustedTask,
    candidate: CandidateRequestV1,
    episode: sqlite3.Row,
    source: sqlite3.Row,
    grant: sqlite3.Row,
    policy: sqlite3.Row,
    reasons: frozenset[str],
) -> EligibilityResult:
    if task.escalation_reason is None:
        return EligibilityResult("INCOMPLETE EVIDENCE", "TRUSTED_REASON_MISSING")
    if task.escalation_reason not in reasons:
        return EligibilityResult("DENY", "ESCALATION_REASON_NOT_PERMITTED")
    recipient = con.execute(
        "SELECT * FROM contacts WHERE contact_id=?", (policy["internal_recipient_id"],)
    ).fetchone()
    if recipient is None:
        return EligibilityResult("INCOMPLETE EVIDENCE", "INTERNAL_RECIPIENT_MISSING")
    if (
        recipient["org_id"] != episode["org_id"]
        or recipient["channel"] != "SYNTHETIC_INTERNAL_QUEUE"
        or not recipient["verified"]
        or not recipient["active"]
    ):
        return EligibilityResult("DENY", "INTERNAL_RECIPIENT_INVALID")
    if (
        candidate.requested_recipient_text is not None
        and candidate.requested_recipient_text != recipient["address"]
    ):
        return EligibilityResult("DENY", "WRONG_INTERNAL_RECIPIENT_REQUESTED")
    facts = _facts(
        episode=episode,
        source=source,
        grant=grant,
        policy=policy,
        operation="SANITIZED_INTERNAL_ESCALATION",
        account_id=None,
        order_id=None,
        order_version=None,
        order_status=None,
        eta=None,
        recipient=recipient,
        reason=task.escalation_reason,
    )
    return EligibilityResult("REQUEST APPROVAL", "SANITIZED_ESCALATION_ELIGIBLE", facts)
