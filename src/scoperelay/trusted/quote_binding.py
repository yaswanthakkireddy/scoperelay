"""ADR-019 V2: bind untrusted exact quotations to the actual A observation.

Binding certifies source location, not intent, ownership, grant or approval.
"""

import json
import re
import sqlite3

from scoperelay.agents.contracts import CandidateInterpretationV2
from scoperelay.storage.source_spans import resolve_span, source_digest


class QuoteBindingError(ValueError):
    def __init__(self, code: str, *, relevance: str = "NOT_RUN") -> None:
        super().__init__(code)
        self.code = code
        self.relevance = relevance


def _relevant(candidate: CandidateInterpretationV2, quote: str) -> bool:
    """Closed necessary support checks, deliberately not an intent classifier."""
    lower = quote.casefold()
    if re.search(
        r"\b(system|developer|manager|assistant)\s*(?:override|:|approved)", lower
    ):
        return False
    if any(
        phrase in lower
        for phrase in ("quoted example", "old message", "not asking", "not a request")
    ):
        return False
    if (
        candidate.uncertainty.ambiguous
        or candidate.uncertainty.quoted_only
        or candidate.uncertainty.conflicting_references
    ):
        return False
    if candidate.operation_hint == "ROUTINE_ORDER_STATUS":
        order = candidate.order_ref_text
        if not order or not re.fullmatch(r"ORD-[0-9]+", order):
            return False
        if not re.search(
            rf"(?<![A-Za-z0-9-]){re.escape(order)}(?![A-Za-z0-9-])", quote
        ):
            return False
        # Identifier-only citations cannot support the proposed operation.
        return bool(
            re.search(
                r"\b(status|tracking|track|delivery|arrival|eta|shipping|shipped)\b",
                lower,
            )
        ) and bool(
            re.search(
                r"\b(check|tell|what|where|update|need|could|please|can|send)\b", lower
            )
        )
    if candidate.operation_hint == "SANITIZED_INTERNAL_ESCALATION":
        return bool(
            re.search(r"\b(escalate|escalation|senior support|manual review)\b", lower)
        )
    return True  # Unsupported/uncertain hypotheses still go through eligibility.


def bind_exact_quotes(
    con: sqlite3.Connection,
    *,
    episode_id: str,
    invocation_id: str,
    tool_event_id: str,
    candidate: CandidateInterpretationV2,
) -> list[dict[str, object]]:
    event = con.execute(
        "SELECT t.*,i.episode_id,i.agent_role,i.turn_evidence_json,e.ticket_id,"
        "e.source_version AS episode_source_version,tk.current_source_version "
        "FROM tool_events t JOIN agent_invocations i ON i.invocation_id=t.invocation_id "
        "JOIN episodes e ON e.episode_id=i.episode_id "
        "JOIN tickets tk ON tk.ticket_id=e.ticket_id WHERE t.tool_event_id=?",
        (tool_event_id,),
    ).fetchone()
    if (
        event is None
        or event["invocation_id"] != invocation_id
        or event["episode_id"] != episode_id
        or event["agent_role"] != "A"
        or event["tool_name"] != "read_assigned_ticket"
        or event["tool_status"] != "SUCCESS"
        or event["observation_owner"] != "TRUSTED_TICKET_REPOSITORY"
        or event["observation_sent_turn"] != 1
        or event["continuation_turn"] != 2
    ):
        raise QuoteBindingError("A_TOOL_CORRELATION_INVALID")
    turns = json.loads(event["turn_evidence_json"])
    if (
        len(turns) != 2
        or turns[0].get("turn") != 1
        or turns[1].get("turn") != 2
        or event["call_id"] not in turns[0].get("native_tool_call_ids", [])
        or turns[1].get("native_tool_call_count") != 0
    ):
        raise QuoteBindingError("A_CONTINUATION_INVALID")
    obs = json.loads(event["observation_json"])
    if (
        obs.get("tool_call_id") != event["call_id"]
        or obs.get("tool_event_id") != tool_event_id
        or obs.get("ticket_id") != event["ticket_id"]
        or candidate.ticket_id != event["ticket_id"]
        or obs.get("source_version") != event["episode_source_version"]
        or event["current_source_version"] != event["episode_source_version"]
        or obs.get("trust_origin") != "CUSTOMER_UNTRUSTED"
    ):
        raise QuoteBindingError("SOURCE_SCOPE_OR_VERSION_INVALID")
    source = con.execute(
        "SELECT source_id,body_text,body_sha256 FROM ticket_sources "
        "WHERE ticket_id=? AND source_version=?",
        (event["ticket_id"], event["episode_source_version"]),
    ).fetchone()
    if (
        source is None
        or obs.get("source_id") != source["source_id"]
        or obs.get("source_digest") != source["body_sha256"]
        or obs.get("body_text") != source["body_text"]
        or source_digest(source["body_text"]) != source["body_sha256"]
    ):
        raise QuoteBindingError("SOURCE_SNAPSHOT_INVALID")
    body = source["body_text"]
    spans: list[dict[str, object]] = []
    for selection in candidate.source_quotes:
        if (
            selection.source_id != source["source_id"]
            or selection.source_version != event["episode_source_version"]
            or selection.source_digest != source["body_sha256"]
        ):
            raise QuoteBindingError("MODEL_SOURCE_IDENTITY_MISMATCH")
        quote = selection.quotation
        if not quote.strip():
            raise QuoteBindingError("EMPTY_QUOTATION")
        start = body.find(quote)
        if start < 0:
            raise QuoteBindingError("SOURCE_QUOTE_ABSENT")
        if body.find(quote, start + 1) >= 0:
            raise QuoteBindingError("SOURCE_QUOTE_AMBIGUOUS")
        if not _relevant(candidate, quote):
            raise QuoteBindingError("SOURCE_QUOTE_NOT_RELEVANT", relevance="FAILED")
        end = start + len(quote)
        resolve_span(
            expected_ticket_id=event["ticket_id"],
            expected_source_version=event["episode_source_version"],
            ticket_id=candidate.ticket_id,
            source_version=selection.source_version,
            body_text=body,
            body_sha256=source["body_sha256"],
            start=start,
            end=end,
            excerpt=quote,
        )
        spans.append(
            {
                "source_id": selection.source_id,
                "source_version": selection.source_version,
                "start": start,
                "end": end,
                "excerpt": quote,
            }
        )
    return spans
