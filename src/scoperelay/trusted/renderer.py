"""Fixed, no-effect material previews (API-105/106 preparation, not admission)."""

from dataclasses import dataclass
from datetime import date, datetime
from hashlib import sha256
import json
import re
import unicodedata
import sqlite3

from scoperelay.storage.fixture_loader import business_key

from .authorization import evaluate
from .contracts import CandidateRequestV1, TrustedTask, VerifiedFacts


class MaterialError(ValueError):
    pass


CANONICAL_KEYS = frozenset(
    {
        "schema_version",
        "fixture_epoch",
        "org_id",
        "grant_id",
        "grant_version",
        "policy_version",
        "ticket_id",
        "source_version",
        "assignment_version",
        "business_interaction_id",
        "request_ordinal",
        "operation_class",
        "account_id",
        "order_id",
        "order_version",
        "recipient_contact_id",
        "recipient_version",
        "channel",
        "business_key",
        "action_id",
        "template_id",
        "field_values",
        "rendered_body",
    }
)
ROUTINE_ORDER = ("order_id", "order_status", "customer_visible_eta")
INTERNAL_ORDER = ("ticket_id", "order_id", "reason_category", "summary_code")


def _nfc(value: object) -> object:
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, list):
        return [_nfc(v) for v in value]
    if isinstance(value, dict):
        return {k: _nfc(v) for k, v in value.items()}
    if value is None or type(value) is int:
        return value
    raise MaterialError("unsupported canonical value type")


def canonical_bytes(payload: dict[str, object]) -> bytes:
    """Exact FILE 15 §I key set, NFC scalar strings and compact sorted UTF-8 JSON."""
    if (
        set(payload) != CANONICAL_KEYS
        or payload["schema_version"] != 1
        or payload["request_ordinal"] != 1
    ):
        raise MaterialError("invalid canonical material schema")
    fields = payload["field_values"]
    if not isinstance(fields, list) or not fields:
        raise MaterialError("field_values must be a nonempty array")
    operation = payload["operation_class"]
    order = (
        ROUTINE_ORDER
        if operation == "ROUTINE_ORDER_STATUS"
        else INTERNAL_ORDER
        if operation == "SANITIZED_INTERNAL_ESCALATION"
        else None
    )
    if order is None:
        raise MaterialError("unsupported operation")
    ids = []
    for item in fields:
        if (
            not isinstance(item, dict)
            or set(item) != {"field_id", "value"}
            or type(item["field_id"]) is not str
        ):
            raise MaterialError("invalid field_values entry")
        ids.append(item["field_id"])
    if (
        len(ids) != len(set(ids))
        or any(i not in order for i in ids)
        or ids != sorted(ids, key=order.index)
    ):
        raise MaterialError("field_values outside fixed catalog order")
    normalized = _nfc(payload)
    try:
        return json.dumps(
            normalized,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except UnicodeError as exc:
        raise MaterialError("invalid Unicode scalar") from exc


@dataclass(frozen=True)
class PreparedMaterial:
    operation_class: str
    action_id: str
    business_key: str
    recipient_contact_id: str
    template_id: str
    field_values: tuple[tuple[str, str | None], ...]
    rendered_body: str
    canonical_json: str
    material_sha256: str
    binding_versions_json: str


def _prepare(
    facts: VerifiedFacts,
    action_id: str,
    template: str,
    values: tuple[tuple[str, str | None], ...],
    body: str,
) -> PreparedMaterial:
    if not action_id or len(action_id) > 128:
        raise MaterialError("invalid trusted action identity")
    key = business_key(
        org_id=facts.org_id,
        interaction_id=facts.business_interaction_id,
        operation=facts.operation_class,
        epoch=facts.fixture_epoch,
    )
    field_values = [{"field_id": k, "value": v} for k, v in values]
    payload: dict[str, object] = {
        "schema_version": 1,
        "fixture_epoch": facts.fixture_epoch,
        "org_id": facts.org_id,
        "grant_id": facts.grant_id,
        "grant_version": facts.grant_version,
        "policy_version": facts.policy_version,
        "ticket_id": facts.ticket_id,
        "source_version": facts.source_version,
        "assignment_version": facts.assignment_version,
        "business_interaction_id": facts.business_interaction_id,
        "request_ordinal": 1,
        "operation_class": facts.operation_class,
        "account_id": facts.account_id,
        "order_id": facts.order_id,
        "order_version": facts.order_version,
        "recipient_contact_id": facts.recipient_contact_id,
        "recipient_version": facts.recipient_version,
        "channel": facts.channel,
        "business_key": key,
        "action_id": action_id,
        "template_id": template,
        "field_values": field_values,
        "rendered_body": body,
    }
    encoded = canonical_bytes(payload)
    normalized = json.loads(encoded)
    versions = {
        "grant_version": facts.grant_version,
        "policy_version": facts.policy_version,
        "source_version": facts.source_version,
        "assignment_version": facts.assignment_version,
        "order_version": facts.order_version,
        "recipient_version": facts.recipient_version,
    }
    return PreparedMaterial(
        facts.operation_class,
        action_id,
        key,
        facts.recipient_contact_id,
        template,
        tuple((item["field_id"], item["value"]) for item in normalized["field_values"]),
        normalized["rendered_body"],
        encoded.decode("utf-8"),
        sha256(encoded).hexdigest(),
        json.dumps(versions, sort_keys=True, separators=(",", ":")),
    )


def render_routine(
    con: sqlite3.Connection,
    task: TrustedTask,
    candidate: CandidateRequestV1,
    *,
    at: datetime,
    action_id: str,
    purpose: str,
    template_id: str,
    selected_field_ids: tuple[str, ...],
) -> PreparedMaterial:
    """Re-read preliminary trusted facts; never accept caller-authored fact DTOs."""
    result = evaluate(con, task, candidate, at=at)
    if result.decision != "ALLOW" or result.facts is None:
        raise MaterialError(f"routine not eligible: {result.reason_code}")
    policy_fields = con.execute(
        "SELECT routine_field_allowlist_json FROM org_policy WHERE org_id=?",
        (result.facts.org_id,),
    ).fetchone()
    if policy_fields is None or not set(selected_field_ids) <= set(
        json.loads(policy_fields[0])
    ):
        raise MaterialError("selected field not permitted by current policy")
    return _render_routine(
        result.facts,
        action_id=action_id,
        purpose=purpose,
        template_id=template_id,
        selected_field_ids=selected_field_ids,
    )


def _render_routine(
    facts: VerifiedFacts,
    *,
    action_id: str,
    purpose: str,
    template_id: str,
    selected_field_ids: tuple[str, ...],
) -> PreparedMaterial:
    if (
        facts.operation_class != "ROUTINE_ORDER_STATUS"
        or facts.channel != "SYNTHETIC_CUSTOMER_QUEUE"
    ):
        raise MaterialError("not an authorized routine recipient")
    if not facts.order_id or not facts.order_status or facts.order_version is None:
        raise MaterialError("trusted order facts missing")
    pairings = {
        ("STATUS_BRIEF", "STATUS_T1"): ("order_id", "order_status"),
        ("STATUS_WITH_ETA", "STATUS_T2"): ROUTINE_ORDER,
        ("STATUS_NO_ETA", "STATUS_NOETA_T1"): ("order_id", "order_status"),
    }
    required = pairings.get((purpose, template_id))
    if required is None or selected_field_ids != required:
        raise MaterialError("unsupported purpose/template/field combination")
    if purpose == "STATUS_WITH_ETA":
        if not facts.customer_visible_eta:
            raise MaterialError("verified ETA is absent")
        if (
            re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", facts.customer_visible_eta)
            is None
        ):
            raise MaterialError("invalid customer-visible ETA format")
        try:
            date.fromisoformat(facts.customer_visible_eta)
        except ValueError as exc:
            raise MaterialError("invalid customer-visible ETA") from exc
    if purpose == "STATUS_NO_ETA" and facts.customer_visible_eta is not None:
        raise MaterialError("no-ETA template with available ETA")
    oid = unicodedata.normalize("NFC", facts.order_id)
    status = unicodedata.normalize("NFC", facts.order_status)
    if any(c in oid for c in "\r\n<>") or len(oid) > 64:
        raise MaterialError("unsafe order identifier")
    if purpose == "STATUS_BRIEF":
        body = f"Order {oid} is {status}."
    elif purpose == "STATUS_WITH_ETA":
        body = (
            f"Order {oid} is {status}. Estimated arrival: {facts.customer_visible_eta}."
        )
    else:
        body = f"Order {oid} is {status}. No ETA is available."
    actual = {
        "order_id": oid,
        "order_status": status,
        "customer_visible_eta": facts.customer_visible_eta,
    }
    return _prepare(
        facts,
        action_id,
        template_id,
        tuple((field, actual[field]) for field in required),
        body,
    )


def render_escalation(
    con: sqlite3.Connection,
    task: TrustedTask,
    candidate: CandidateRequestV1,
    *,
    at: datetime,
    action_id: str,
) -> PreparedMaterial:
    result = evaluate(con, task, candidate, at=at)
    if result.decision != "REQUEST APPROVAL" or result.facts is None:
        raise MaterialError(f"escalation not eligible: {result.reason_code}")
    policy_fields = con.execute(
        "SELECT internal_field_allowlist_json FROM org_policy WHERE org_id=?",
        (result.facts.org_id,),
    ).fetchone()
    if policy_fields is None or not {
        "ticket_id",
        "reason_category",
        "summary_code",
    } <= set(json.loads(policy_fields[0])):
        raise MaterialError("internal fields not permitted by current policy")
    return _render_escalation(result.facts, action_id=action_id)


def _render_escalation(facts: VerifiedFacts, *, action_id: str) -> PreparedMaterial:
    if (
        facts.operation_class != "SANITIZED_INTERNAL_ESCALATION"
        or facts.channel != "SYNTHETIC_INTERNAL_QUEUE"
        or facts.reason_category not in {"STATUS_CONFLICT", "MANUAL_REVIEW_REQUIRED"}
    ):
        raise MaterialError("not an eligible sanitized escalation")
    ticket = unicodedata.normalize("NFC", facts.ticket_id)
    if any(c in ticket for c in "\r\n<>") or len(ticket) > 64:
        raise MaterialError("unsafe ticket identifier")
    values = (
        ("ticket_id", ticket),
        ("reason_category", facts.reason_category),
        ("summary_code", "REVIEW_REQUIRED"),
    )
    return _prepare(
        facts,
        action_id,
        "ESCALATE_FIXED_V1",
        values,
        f"Ticket {ticket} requires manual status review.",
    )
