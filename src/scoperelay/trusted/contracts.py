"""Closed, typed inputs and outputs for preliminary trusted decisions."""

from dataclasses import dataclass
from typing import Any, Mapping


class CandidateShapeError(ValueError):
    pass


class TrustedStateError(RuntimeError):
    """Corrupt or internally inconsistent trusted state, not a business DENY."""


@dataclass(frozen=True)
class SourceSpan:
    source_id: str
    source_version: int
    start: int
    end: int
    excerpt: str


@dataclass(frozen=True)
class CandidateRequestV1:
    """Untrusted interpretation. No grant/recipient/contact authority fields exist."""

    candidate_id: str
    ticket_id: str
    operation_hint: str
    order_ref_text: str | None
    requested_recipient_text: str | None
    content_class_hint: str | None
    source_spans: tuple[SourceSpan, ...]
    ambiguous: bool
    quoted_only: bool
    conflicting_references: tuple[str, ...]
    other_candidates: tuple[object, ...]

    def __post_init__(self) -> None:
        if (
            type(self.candidate_id) is not str
            or not self.candidate_id
            or type(self.ticket_id) is not str
            or not self.ticket_id
            or self.operation_hint
            not in {
                "ROUTINE_ORDER_STATUS",
                "SANITIZED_INTERNAL_ESCALATION",
                "UNSUPPORTED_REQUEST",
                "UNCERTAIN",
            }
            or not self.source_spans
            or any(type(span) is not SourceSpan for span in self.source_spans)
            or type(self.ambiguous) is not bool
            or type(self.quoted_only) is not bool
        ):
            raise CandidateShapeError("invalid direct candidate construction")

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "CandidateRequestV1":
        required = {
            "candidate_id",
            "ticket_id",
            "operation_hint",
            "order_ref_text",
            "requested_recipient_text",
            "content_class_hint",
            "source_spans",
            "uncertainty",
            "other_candidates",
        }
        if set(raw) != required:
            raise CandidateShapeError("candidate fields must match V1 exactly")
        if any(
            type(raw[key]) is not str or not raw[key]
            for key in ("candidate_id", "ticket_id", "operation_hint")
        ):
            raise CandidateShapeError("invalid candidate identity or operation label")
        for key in ("order_ref_text", "requested_recipient_text", "content_class_hint"):
            if raw[key] is not None and type(raw[key]) is not str:
                raise CandidateShapeError(f"invalid {key}")
        if raw["operation_hint"] not in {
            "ROUTINE_ORDER_STATUS",
            "SANITIZED_INTERNAL_ESCALATION",
            "UNSUPPORTED_REQUEST",
            "UNCERTAIN",
        }:
            raise CandidateShapeError("unknown extraction label")
        uncertainty = raw["uncertainty"]
        if not isinstance(uncertainty, dict) or set(uncertainty) != {
            "ambiguous",
            "quoted_only",
            "conflicting_references",
        }:
            raise CandidateShapeError("invalid uncertainty shape")
        if (
            type(uncertainty["ambiguous"]) is not bool
            or type(uncertainty["quoted_only"]) is not bool
        ):
            raise CandidateShapeError("invalid uncertainty flags")
        refs = uncertainty["conflicting_references"]
        if not isinstance(refs, list) or any(type(x) is not str for x in refs):
            raise CandidateShapeError("invalid conflicting references")
        spans_raw = raw["source_spans"]
        if not isinstance(spans_raw, list) or not spans_raw:
            raise CandidateShapeError("at least one source span required")
        spans = []
        for value in spans_raw:
            if not isinstance(value, dict) or set(value) != {
                "source_id",
                "source_version",
                "start",
                "end",
                "excerpt",
            }:
                raise CandidateShapeError("invalid source span shape")
            if any(type(value[k]) is not str for k in ("source_id", "excerpt")) or any(
                type(value[k]) is not int for k in ("source_version", "start", "end")
            ):
                raise CandidateShapeError("invalid source span types")
            spans.append(SourceSpan(**value))
        others = raw["other_candidates"]
        if not isinstance(others, list):
            raise CandidateShapeError("invalid other candidates")
        return cls(
            raw["candidate_id"],
            raw["ticket_id"],
            raw["operation_hint"],
            raw["order_ref_text"],
            raw["requested_recipient_text"],
            raw["content_class_hint"],
            tuple(spans),
            uncertainty["ambiguous"],
            uncertainty["quoted_only"],
            tuple(refs),
            tuple(others),
        )


@dataclass(frozen=True)
class TrustedTask:
    """Server-owned expectation captured when starting an episode, never model JSON."""

    episode_id: str
    assignment_version: int
    escalation_reason: str | None = None


@dataclass(frozen=True)
class VerifiedFacts:
    episode_id: str
    fixture_epoch: str
    org_id: str
    policy_version: int
    grant_id: str
    grant_version: int
    ticket_id: str
    source_id: str
    source_version: int
    source_digest: str
    assignment_version: int
    business_interaction_id: str
    operation_class: str
    customer_id: str
    account_id: str | None
    order_id: str | None
    order_version: int | None
    order_status: str | None
    customer_visible_eta: str | None
    recipient_contact_id: str
    recipient_version: int
    channel: str
    reason_category: str | None


@dataclass(frozen=True)
class EligibilityResult:
    decision: str
    reason_code: str
    facts: VerifiedFacts | None = None
    clarification_draft: str | None = None

    def __post_init__(self) -> None:
        if self.decision not in {
            "ALLOW",
            "DENY",
            "CLARIFY",
            "REQUEST APPROVAL",
            "INCOMPLETE EVIDENCE",
        }:
            raise ValueError("invalid workflow decision")
