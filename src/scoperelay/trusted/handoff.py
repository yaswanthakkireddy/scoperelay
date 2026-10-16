"""No-effect H1/H2/H3/H4 projection; no agent invocation or persisted handoff."""

from dataclasses import dataclass
from datetime import datetime
import sqlite3

from .authorization import evaluate
from .contracts import CandidateRequestV1, TrustedTask


@dataclass(frozen=True)
class HandoffPreview:
    h1_grant_ref: dict[str, object]
    h2_verified_refs: dict[str, object]
    h3_candidate: dict[str, object]
    h4_source_refs: tuple[dict[str, object], ...]


def make_handoff_preview(
    con: sqlite3.Connection,
    task: TrustedTask,
    candidate: CandidateRequestV1,
    *,
    at: datetime,
) -> HandoffPreview:
    result = evaluate(con, task, candidate, at=at)
    if result.decision not in {"ALLOW", "REQUEST APPROVAL"} or result.facts is None:
        raise ValueError("no eligible trusted handoff")
    facts = result.facts
    h1 = {
        "grant_id": facts.grant_id,
        "org_id": facts.org_id,
        "operation_class": facts.operation_class,
        "grant_version": facts.grant_version,
        "policy_version": facts.policy_version,
        "ticket_id": facts.ticket_id,
        "source_version": facts.source_version,
        "assignment_version": facts.assignment_version,
        "business_interaction_id": facts.business_interaction_id,
    }
    h2 = {
        "customer_id": facts.customer_id,
        "account_id": facts.account_id,
        "order_id": facts.order_id,
        "order_version": facts.order_version,
        "order_status": facts.order_status,
        "customer_visible_eta": facts.customer_visible_eta,
        "recipient_contact_id": facts.recipient_contact_id,
        "recipient_version": facts.recipient_version,
        "channel": facts.channel,
        "reason_category": facts.reason_category,
    }
    h3 = {
        "candidate_id": candidate.candidate_id,
        "operation_hint": candidate.operation_hint,
        "order_ref_text": candidate.order_ref_text,
        "requested_recipient_text": candidate.requested_recipient_text,
        "content_class_hint": candidate.content_class_hint,
        "ambiguous": candidate.ambiguous,
        "quoted_only": candidate.quoted_only,
    }
    h4 = tuple(
        {
            "source_id": span.source_id,
            "source_version": span.source_version,
            "source_digest": facts.source_digest,
            "start": span.start,
            "end": span.end,
            "excerpt": span.excerpt,
        }
        for span in candidate.source_spans
    )
    return HandoffPreview(h1, h2, h3, h4)
