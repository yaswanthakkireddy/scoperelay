"""Strict model JSON branches; IDs and authority are not model-controlled."""

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Span(Strict):
    source_id: str
    source_version: int
    start: int
    end: int
    excerpt: str = Field(min_length=1, max_length=1024)


class Uncertainty(Strict):
    ambiguous: bool
    quoted_only: bool
    conflicting_references: list[str]


class CandidateJSON(Strict):
    candidate_id: str
    ticket_id: str
    operation_hint: Literal[
        "ROUTINE_ORDER_STATUS",
        "SANITIZED_INTERNAL_ESCALATION",
        "UNSUPPORTED_REQUEST",
        "UNCERTAIN",
    ]
    order_ref_text: str | None
    requested_recipient_text: str | None
    content_class_hint: str | None
    source_spans: list[Span] = Field(min_length=1, max_length=8)
    uncertainty: Uncertainty
    other_candidates: list[dict] = Field(max_length=4)


class SourceQuoteV2(Strict):
    source_id: str = Field(min_length=1, max_length=80)
    source_version: int = Field(ge=1)
    source_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    quotation: str = Field(min_length=1, max_length=1024)


class CandidateInterpretationV2(Strict):
    candidate_id: str = Field(min_length=1)
    ticket_id: str = Field(min_length=1)
    operation_hint: Literal[
        "ROUTINE_ORDER_STATUS",
        "SANITIZED_INTERNAL_ESCALATION",
        "UNSUPPORTED_REQUEST",
        "UNCERTAIN",
    ]
    order_ref_text: str | None
    requested_recipient_text: str | None
    content_class_hint: str | None
    source_quotes: list[SourceQuoteV2] = Field(min_length=1, max_length=8)
    uncertainty: Uncertainty
    other_candidates: list[dict] = Field(max_length=4)


class RoutineProposal(Strict):
    status: Literal["PROPOSED"]
    purpose: Literal["STATUS_BRIEF", "STATUS_WITH_ETA", "STATUS_NO_ETA"]
    template_variant: Literal["STATUS_T1", "STATUS_T2", "STATUS_NOETA_T1"]
    selected_field_ids: list[
        Literal["order_id", "order_status", "customer_visible_eta"]
    ] = Field(min_length=1)
    observation_ref: str


class NonmaterialProposal(Strict):
    status: Literal["ESCALATION_ACK", "REFUSED", "UNABLE"]
    nonmaterial_note: str | None = Field(default=None, max_length=280)


class AgentAFailure(Strict):
    status: Literal["REFUSED", "UNABLE"]
    nonmaterial_note: str | None = Field(default=None, max_length=280)


def agent_a_v2_response_schema() -> dict:
    """Canonical success-or-inability schema; trusted parsing still checks each branch."""
    return TypeAdapter(CandidateInterpretationV2 | AgentAFailure).json_schema()


def agent_b_response_schema() -> dict:
    """Best-effort wire shape only; trusted B parser still enforces the contract."""
    return TypeAdapter(RoutineProposal | NonmaterialProposal).json_schema()
