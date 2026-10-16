"""Task 05A read-only A/B orchestration. No protected-resource import or call."""

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from time import monotonic
from uuid import uuid4

from pydantic import ValidationError

from scoperelay.agents.contracts import (
    AgentAFailure,
    CandidateInterpretationV2,
    agent_a_v2_response_schema,
    CandidateJSON,
    NonmaterialProposal,
    RoutineProposal,
)
from scoperelay.agents.prompts import (
    PROMPT_A_OUTPUT_CONTRACT_V1,
    PROMPT_A_OUTPUT_CONTRACT_V2,
    PROMPT_A_SYSTEM_V1,
    PROMPT_A_SYSTEM_V2,
    PROMPT_B_OUTPUT_CONTRACT_V1,
    PROMPT_B_SYSTEM_V1,
)
from scoperelay.inference.adapter import (
    ChatTransport,
    ModelTurn,
    ProviderAdapter,
    ProviderFailure,
)
from scoperelay.storage.database import open_existing
from scoperelay.storage.source_spans import SourceReferenceError
from scoperelay.storage.transaction import immediate
from scoperelay.trusted.approval import read_approved_escalation_context
from scoperelay.trusted.authorization import evaluate
from scoperelay.trusted.contracts import CandidateRequestV1, TrustedTask
from scoperelay.trusted.contracts import CandidateShapeError
from scoperelay.trusted.handoff import make_handoff_preview
from scoperelay.trusted.tool_dispatcher import ToolScope, ToolScopeDenied, dispatch
from scoperelay.trusted.quote_binding import QuoteBindingError, bind_exact_quotes


class AgentExecutionError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReadOnlyResult:
    episode_id: str
    a_invocation_id: str
    b_invocation_id: str | None
    decision: str | None
    b_status: str | None
    run_kind: str
    protected_effect: str = "DISABLED"
    execution_state: str | None = None


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _content(turn: ModelTurn) -> dict:
    if turn.tool_calls:
        raise AgentExecutionError("nonfinal structured continuation")
    if not turn.content:
        raise AgentExecutionError("missing model content")
    if len(turn.content) > 16384:
        raise AgentExecutionError("oversized model content")
    try:
        value = json.loads(turn.content)
    except ValueError as exc:
        if (
            turn.content.lstrip().startswith(("```", "Here", "{"))
            and "{" in turn.content
            and "}" in turn.content
        ):
            if (
                not turn.content.lstrip().startswith("{")
                or turn.content.rstrip()[-1:] != "}"
            ):
                raise AgentExecutionError("JSON with surrounding text") from exc
        raise AgentExecutionError("malformed model JSON") from exc
    if type(value) is not dict:
        raise AgentExecutionError("model response must be an object")
    return value


_SAFE_PATHS = {
    "candidate_id",
    "ticket_id",
    "operation_hint",
    "order_ref_text",
    "requested_recipient_text",
    "content_class_hint",
    "source_spans",
    "source_quotes",
    "source_digest",
    "quotation",
    "source_id",
    "source_version",
    "start",
    "end",
    "excerpt",
    "uncertainty",
    "ambiguous",
    "quoted_only",
    "conflicting_references",
    "other_candidates",
    "status",
    "nonmaterial_note",
    "purpose",
    "template_variant",
    "selected_field_ids",
    "observation_ref",
}


def _json_type(value: object) -> str:
    if value is None:
        return "null"
    if type(value) is bool:
        return "boolean"
    if type(value) is dict:
        return "object"
    if type(value) is list:
        return "array"
    if type(value) is str:
        return "string"
    if type(value) in (int, float):
        return "number"
    return "unknown"


def _validation_diagnostic(exc: Exception, stage: str, raw: dict | None = None) -> dict:
    result = {"stage": stage, "category": type(exc).__name__}
    if isinstance(exc, ValidationError):
        result["errors"] = []
        for item in exc.errors(include_input=False, include_context=False)[:12]:
            loc = item["loc"][:5]
            path = [
                part if isinstance(part, int) or part in _SAFE_PATHS else "<untrusted>"
                for part in loc
            ]
            detail = {"type": item["type"], "path": path}
            if raw is not None and all(part != "<untrusted>" for part in path):
                value = raw
                found = True
                for part in loc:
                    if isinstance(value, dict) and part in value:
                        value = value[part]
                    elif (
                        isinstance(value, list)
                        and type(part) is int
                        and 0 <= part < len(value)
                    ):
                        value = value[part]
                    else:
                        found = False
                        break
                if found:
                    detail["observed_json_type"] = _json_type(value)
            if item["type"] == "model_type":
                detail["expected_json_type"] = "object"
            result["errors"].append(detail)
    elif isinstance(exc, SourceReferenceError):
        result["source_error"] = str(exc)
    elif isinstance(exc, QuoteBindingError):
        result["reason"] = exc.code
    elif isinstance(exc, AgentExecutionError):
        result["reason"] = str(exc)
    elif isinstance(exc, CandidateShapeError):
        result["reason"] = str(exc)
    return result


class ReadOnlyAgentRunner:
    def __init__(
        self,
        db_path: Path,
        transport: ChatTransport,
        *,
        max_tokens: int = 768,
        max_duration_seconds: float = 90,
    ) -> None:
        if transport.run_kind not in {
            "LIVE",
            "DETERMINISTIC_COMPONENT_TEST",
            "RECORDED_REPLAY",
        }:
            raise ValueError("unknown execution mode")
        if transport.run_kind == "LIVE" and type(transport) is not ProviderAdapter:
            raise ValueError("test or replay transport cannot claim LIVE")
        if max_tokens < 128 or max_tokens > 2048 or max_duration_seconds <= 0:
            raise ValueError("invalid inference bounds")
        self.path, self.transport = db_path, transport
        self.max_tokens, self.max_duration = max_tokens, max_duration_seconds

    def run(
        self,
        episode_id: str,
        *,
        trusted_escalation_reason: str | None = None,
        material_action_id: str | None = None,
        approval_id: str | None = None,
    ) -> ReadOnlyResult:
        start = monotonic()
        con = open_existing(self.path)
        a_id, b_id = uuid4().hex, None
        stage = "episode_preflight"
        protocol = None
        raw_for_diagnostic = None
        try:
            con.row_factory = __import__("sqlite3").Row
            episode = con.execute(
                "SELECT * FROM episodes WHERE episode_id=?", (episode_id,)
            ).fetchone()
            if episode is None or episode["run_kind"] != self.transport.run_kind:
                raise AgentExecutionError("episode/run mode mismatch")
            if con.execute(
                "SELECT 1 FROM agent_invocations WHERE episode_id=?", (episode_id,)
            ).fetchone():
                raise AgentExecutionError("episode already has agent evidence")
            pin = con.execute(
                "SELECT protocol_version FROM episode_candidate_protocols "
                "WHERE episode_id=?",
                (episode_id,),
            ).fetchone()
            if pin is None:
                raise AgentExecutionError(
                    "candidate protocol not pinned before invocation"
                )
            protocol = pin[0]
            if protocol not in {"V1_SPANS", "V2_EXACT_QUOTE"}:
                raise AgentExecutionError("unsupported candidate protocol")
            scope_a = ToolScope(
                "A", episode_id, episode["grant_id"], episode["ticket_id"]
            )
            self._begin(con, a_id, episode_id, "A")
            a_prompt = (
                (PROMPT_A_SYSTEM_V2 + "\n\n" + PROMPT_A_OUTPUT_CONTRACT_V2)
                if protocol == "V2_EXACT_QUOTE"
                else (PROMPT_A_SYSTEM_V1 + "\n\n" + PROMPT_A_OUTPUT_CONTRACT_V1)
            )
            a_messages = [
                {"role": "system", "content": a_prompt},
                {
                    "role": "user",
                    "content": f"Assigned ticket_id: {episode['ticket_id']}. Call read_assigned_ticket for this ID.",
                },
            ]
            stage = "A_tool_loop"
            a_first, a_turn, a_event, a_obs = self._tool_loop(
                con,
                a_id,
                scope_a,
                a_messages,
                start,
                response_protocol="V2_EXACT_QUOTE"
                if protocol == "V2_EXACT_QUOTE"
                else None,
            )
            stage = "A_output_parse"
            raw = {"status": "REFUSED"} if a_turn.refusal else _content(a_turn)
            raw_for_diagnostic = raw
            self._mark_last(
                con,
                a_id,
                json_parse_status=(
                    "PROVIDER_REFUSAL_NO_JSON_PARSE"
                    if a_turn.refusal
                    else "PARSED_OBJECT"
                ),
            )
            if raw.get("status") in {"REFUSED", "UNABLE"}:
                failure = AgentAFailure.model_validate(raw)
                self._finish(
                    con,
                    a_id,
                    a_first,
                    None if a_event is None else a_turn,
                    a_event,
                    status=failure.status,
                )
                return ReadOnlyResult(
                    episode_id,
                    a_id,
                    None,
                    None,
                    failure.status,
                    self.transport.run_kind,
                )
            if a_event is None:
                raise AgentExecutionError(
                    "A candidate has no actual ticket observation"
                )
            binding_id = None
            model_quotes = None
            spans = None
            stage = "A_candidate_schema"
            if protocol == "V2_EXACT_QUOTE":
                interpretation = CandidateInterpretationV2.model_validate(raw)
                self._mark_last(con, a_id, schema_status="VALID_V2_STRUCTURE")
                model_quotes = [
                    item.model_dump() for item in interpretation.source_quotes
                ]
                binding_id = uuid4().hex
                stage = "A_quote_binding"
                try:
                    spans = bind_exact_quotes(
                        con,
                        episode_id=episode_id,
                        invocation_id=a_id,
                        tool_event_id=a_event,
                        candidate=interpretation,
                    )
                except (QuoteBindingError, SourceReferenceError) as exc:
                    self._mark_last(con, a_id, binding_status="REJECTED")
                    self._quote_audit(
                        con,
                        binding_id,
                        episode_id,
                        a_id,
                        a_event,
                        model_quotes,
                        [],
                        "REJECTED",
                        exc.relevance
                        if isinstance(exc, QuoteBindingError)
                        else "NOT_RUN",
                        exc.code
                        if isinstance(exc, QuoteBindingError)
                        else "SOURCE_SPAN_INVALID",
                    )
                    raise
                self._mark_last(con, a_id, binding_status="EXACT_BOUND_RELEVANT")
                raw = {**raw, "source_spans": spans}
                del raw["source_quotes"]
                raw_for_diagnostic = raw
            CandidateJSON.model_validate(raw)
            candidate = CandidateRequestV1.from_mapping(raw)
            stage = "A_candidate_identity"
            if candidate.ticket_id != episode["ticket_id"]:
                raise AgentExecutionError("candidate changed assigned ticket")
            candidate = CandidateRequestV1.from_mapping(
                {**raw, "candidate_id": uuid4().hex}
            )
            task = TrustedTask(
                episode_id,
                self._assignment(con, episode["ticket_id"]),
                trusted_escalation_reason,
            )
            stage = "A_trusted_validation"
            try:
                decision = evaluate(con, task, candidate, at=datetime.now(timezone.utc))
            except Exception:
                if binding_id is not None:
                    self._quote_audit(
                        con,
                        binding_id,
                        episode_id,
                        a_id,
                        a_event,
                        model_quotes,
                        spans,
                        "REJECTED",
                        "PASSED",
                        "DOWNSTREAM_VALIDATION_FAILED",
                    )
                raise
            if protocol == "V2_EXACT_QUOTE":
                self._mark_last(
                    con, a_id, candidate_status="TRUSTED_VALIDATION_COMPLETE"
                )
            # A source citation is not valid merely because it is well-typed.
            # Do not persist a candidate or mark A complete before the trusted
            # source/version/span checks have finished successfully.
            stage = "A_candidate_persistence"
            with immediate(con):
                con.execute(
                    "INSERT INTO candidate_requests VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        candidate.candidate_id,
                        episode_id,
                        a_id,
                        candidate.operation_hint,
                        candidate.order_ref_text,
                        candidate.requested_recipient_text,
                        candidate.content_class_hint,
                        _json(raw["source_spans"]),
                        _json(raw["uncertainty"]),
                        _json({**raw, "candidate_id": candidate.candidate_id}),
                    ),
                )
                if binding_id is not None:
                    self._quote_audit(
                        con,
                        binding_id,
                        episode_id,
                        a_id,
                        a_event,
                        model_quotes,
                        spans,
                        "VALIDATED",
                        "PASSED",
                        None,
                        candidate.candidate_id,
                    )
                self._finish(con, a_id, a_first, a_turn, a_event)
            if (
                decision.decision not in {"ALLOW", "REQUEST APPROVAL"}
                or decision.facts is None
            ):
                return ReadOnlyResult(
                    episode_id,
                    a_id,
                    None,
                    decision.decision,
                    None,
                    self.transport.run_kind,
                )
            facts = decision.facts
            escalation = facts.operation_class == "SANITIZED_INTERNAL_ESCALATION"
            if escalation and (not material_action_id or not approval_id):
                return ReadOnlyResult(
                    episode_id,
                    a_id,
                    None,
                    decision.decision,
                    None,
                    self.transport.run_kind,
                    execution_state="APPROVAL_CONTEXT_MISSING",
                )
            material_digest = None
            if escalation:
                approval_state = con.execute(
                    "SELECT a.status FROM escalation_approvals a JOIN material_actions m "
                    "ON m.material_action_id=a.material_action_id "
                    "WHERE a.approval_id=? AND m.material_action_id=? AND m.episode_id=?",
                    (approval_id, material_action_id, episode_id),
                ).fetchone()
                if approval_state is not None and approval_state[0] in {
                    "PENDING",
                    "REJECTED",
                }:
                    return ReadOnlyResult(
                        episode_id,
                        a_id,
                        None,
                        decision.decision,
                        None,
                        self.transport.run_kind,
                        execution_state=f"APPROVAL_{approval_state[0]}",
                    )
                material_digest, reason = read_approved_escalation_context(
                    con,
                    episode_id=episode_id,
                    material_action_id=material_action_id,
                    approval_id=approval_id,
                    at=datetime.now(timezone.utc),
                )
                if reason != facts.reason_category:
                    raise AgentExecutionError(
                        "approved material reason differs from trusted task"
                    )
            verification_id, handoff_id = uuid4().hex, uuid4().hex
            versions = {
                "order_version": facts.order_version,
                "recipient_version": facts.recipient_version,
            }
            if not escalation:
                con.execute(
                    "INSERT INTO verifications VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        verification_id,
                        episode_id,
                        facts.grant_id,
                        facts.ticket_id,
                        facts.customer_id,
                        facts.account_id,
                        facts.order_id,
                        facts.recipient_contact_id,
                        _json(versions),
                        "VERIFIED",
                        _now(),
                    ),
                )
            preview = make_handoff_preview(
                con, task, candidate, at=datetime.now(timezone.utc)
            )
            h1 = preview.h1_grant_ref
            h3 = (
                {
                    **preview.h3_candidate,
                    "candidate_protocol_version": protocol,
                    "source_binding_id": binding_id,
                }
                if binding_id is not None
                else preview.h3_candidate
            )
            h2 = {
                **preview.h2_verified_refs,
                **(
                    {
                        "material_action_id": material_action_id,
                        "material_sha256": material_digest,
                    }
                    if escalation
                    else {"verification_id": verification_id, "versions": versions}
                ),
            }
            con.execute(
                "INSERT INTO handoffs VALUES (?,?,?,?,?,?,?,?)",
                (
                    handoff_id,
                    episode_id,
                    _json(h1),
                    _json(h2),
                    _json(h3),
                    _json(preview.h4_source_refs),
                    _now(),
                    "VALID",
                ),
            )
            b_id = uuid4().hex
            self._begin(con, b_id, episode_id, "B")
            stage = "B_tool_loop"
            scope_b = ToolScope(
                "B",
                episode_id,
                facts.grant_id,
                facts.ticket_id,
                handoff_id,
                None if escalation else verification_id,
            )
            b_messages = [
                {
                    "role": "system",
                    "content": PROMPT_B_SYSTEM_V1
                    + "\n\n"
                    + PROMPT_B_OUTPUT_CONTRACT_V1,
                },
                {
                    "role": "user",
                    "content": "TRUSTED COORDINATOR H1: "
                    + _json(h1)
                    + " H2: "
                    + _json(h2),
                },
                {
                    "role": "user",
                    "content": "LOWER-TRUST SOURCE DATA ONLY H3: "
                    + _json(h3)
                    + " H4: "
                    + _json(preview.h4_source_refs),
                },
            ]
            if escalation:
                b_first = self._complete(con, b_id, b_messages, None, start)
                if b_first.tool_calls:
                    raise AgentExecutionError(
                        "escalation B must not request inapplicable status tool"
                    )
                b_turn, b_event, b_obs = b_first, None, None
            else:
                b_first, b_turn, b_event, b_obs = self._tool_loop(
                    con, b_id, scope_b, b_messages, start
                )
            stage = "B_output_parse"
            b_raw = {"status": "REFUSED"} if b_turn.refusal else _content(b_turn)
            if b_raw.get("status") == "PROPOSED":
                if escalation:
                    raise AgentExecutionError(
                        "escalation cannot propose routine material"
                    )
                stage = "B_proposal_schema"
                parsed = RoutineProposal.model_validate(b_raw)
                stage = "B_proposal_semantics"
                if parsed.observation_ref != b_event or not b_obs:
                    raise AgentExecutionError(
                        "B observation reference is not actual tool result"
                    )
                fields = parsed.selected_field_ids
                if (
                    len(fields) != len(set(fields))
                    or "order_status" not in fields
                    or (
                        "customer_visible_eta" in fields
                        and b_obs["customer_visible_eta"] is None
                    )
                ):
                    raise AgentExecutionError("B selected unsupported verified fields")
                if parsed.purpose == "STATUS_WITH_ETA" and (
                    b_obs["customer_visible_eta"] is None
                    or "customer_visible_eta" not in fields
                ):
                    raise AgentExecutionError("B claimed unavailable ETA")
                if (
                    parsed.purpose == "STATUS_NO_ETA"
                    and "customer_visible_eta" in fields
                ):
                    raise AgentExecutionError("B no-ETA purpose conflicts with field")
                purpose, variant, selected, observation, note = (
                    parsed.purpose,
                    parsed.template_variant,
                    _json(fields),
                    b_event,
                    None,
                )
            else:
                stage = "B_nonmaterial_schema"
                parsed = NonmaterialProposal.model_validate(b_raw)
                if parsed.status == "ESCALATION_ACK" and not escalation:
                    raise AgentExecutionError("routine B cannot acknowledge escalation")
                purpose = variant = selected = observation = None
                note = parsed.nonmaterial_note
            if escalation:
                current_digest, current_reason = read_approved_escalation_context(
                    con,
                    episode_id=episode_id,
                    material_action_id=material_action_id,
                    approval_id=approval_id,
                    at=datetime.now(timezone.utc),
                )
                if (
                    current_digest != material_digest
                    or current_reason != facts.reason_category
                ):
                    raise AgentExecutionError(
                        "approved escalation changed during B continuation"
                    )
            self._finish(
                con,
                b_id,
                b_first,
                None if b_event is None else b_turn,
                b_event,
                status=parsed.status,
            )
            con.execute(
                "INSERT INTO agent_b_proposals VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    uuid4().hex,
                    episode_id,
                    b_id,
                    handoff_id,
                    purpose,
                    variant,
                    selected,
                    observation,
                    note,
                    parsed.status,
                    1 if b_event is None else 2,
                ),
            )
            return ReadOnlyResult(
                episode_id,
                a_id,
                b_id,
                decision.decision,
                parsed.status,
                self.transport.run_kind,
            )
        except Exception as exc:
            # Preserve fixed, safe runner/provider failure categories without
            # persisting untrusted model text or SDK exception messages.
            failure_code = (
                str(exc)
                if isinstance(exc, (AgentExecutionError, ProviderFailure))
                else type(exc).__name__
            )
            for inv in (a_id, b_id):
                if inv:
                    row = con.execute(
                        "SELECT turn_evidence_json FROM agent_invocations "
                        "WHERE invocation_id=? AND status='STARTED'",
                        (inv,),
                    ).fetchone()
                    if row is None:
                        continue
                    evidence = json.loads(row[0])
                    if inv == (b_id or a_id):
                        if evidence:
                            evidence[-1]["diagnostic"] = _validation_diagnostic(
                                exc, stage, raw_for_diagnostic if inv == a_id else None
                            )
                            if stage in {"A_output_parse", "B_output_parse"}:
                                evidence[-1]["json_parse_status"] = "FAILED"
                            if stage == "A_candidate_schema":
                                evidence[-1]["schema_status"] = (
                                    "INVALID_V2_STRUCTURE"
                                    if (protocol == "V2_EXACT_QUOTE")
                                    else "INVALID_V1_STRUCTURE"
                                )
                        else:
                            evidence.append(
                                {"diagnostic": _validation_diagnostic(exc, stage)}
                            )
                    con.execute(
                        "UPDATE agent_invocations SET status='FAILED',ended_at=?,failure_code=? "
                        ",turn_evidence_json=? WHERE invocation_id=? AND status='STARTED'",
                        (_now(), failure_code, _json(evidence), inv),
                    )
            raise AgentExecutionError(failure_code) from exc
        finally:
            con.close()

    def _assignment(self, con, ticket_id: str) -> int:
        return con.execute(
            "SELECT assignment_version FROM tickets WHERE ticket_id=?", (ticket_id,)
        ).fetchone()[0]

    def _quote_audit(
        self,
        con,
        binding_id: str,
        episode_id: str,
        invocation_id: str,
        tool_event_id: str,
        model_quotes: list[dict],
        spans: list[dict],
        status: str,
        relevance: str,
        diagnostic: str | None,
        candidate_id: str | None = None,
    ) -> None:
        first = model_quotes[0]
        con.execute(
            "INSERT INTO candidate_quote_bindings VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                binding_id,
                episode_id,
                invocation_id,
                tool_event_id,
                "V2_EXACT_QUOTE",
                _json(model_quotes),
                _json(spans),
                first["source_id"],
                first["source_version"],
                first["source_digest"],
                "EXACT_UNIQUE_V1",
                status,
                relevance,
                diagnostic,
                candidate_id,
                _now(),
            ),
        )

    def _begin(self, con, inv: str, episode: str, role: str) -> None:
        con.execute(
            "INSERT INTO agent_invocations VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                inv,
                episode,
                role,
                self.transport.provider,
                self.transport.model,
                int(self.transport.run_kind == "LIVE"),
                "STARTED",
                _now(),
                None,
                None,
                "[]",
            ),
        )

    def _tool_loop(
        self,
        con,
        inv: str,
        scope: ToolScope,
        messages: list[dict],
        start: float,
        response_protocol: str | None = None,
    ) -> tuple[ModelTurn, ModelTurn, str | None, dict | None]:
        if monotonic() - start > self.max_duration:
            raise AgentExecutionError("episode duration exceeded")
        tool = (
            "read_assigned_ticket"
            if scope.role == "A"
            else "read_verified_order_status"
        )
        first = self._complete(con, inv, messages, tool, start)
        if first.finish_reason == "length":
            raise AgentExecutionError("truncated model turn")
        if first.refusal and first.tool_calls:
            raise AgentExecutionError(
                "provider returned conflicting refusal and tool call"
            )
        if not first.tool_calls:
            return first, first, None, None
        if len(first.tool_calls) != 1:
            raise AgentExecutionError("one actual scoped tool call required")
        call = first.tool_calls[0]
        event_id = uuid4().hex
        try:
            obs = dispatch(con, scope, call)
        except ToolScopeDenied:
            # The canonical tool table has a closed name CHECK. Never rewrite an
            # unknown provider-emitted name into an approved name for audit.
            if call.name == tool:
                con.execute(
                    "INSERT INTO tool_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        event_id,
                        inv,
                        call.call_id,
                        call.name,
                        _json({"rejected_arguments": True}),
                        None,
                        None,
                        None,
                        "SCOPE_DENIED",
                        None,
                        None,
                        None,
                        None,
                    ),
                )
            raise
        obs = {"tool_call_id": call.call_id, "tool_event_id": event_id, **obs}
        con.execute(
            "INSERT INTO tool_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                event_id,
                inv,
                call.call_id,
                tool,
                call.arguments,
                _json(obs),
                "TRUSTED_TICKET_REPOSITORY"
                if scope.role == "A"
                else "TRUSTED_ORDER_REPOSITORY",
                _now(),
                "SUCCESS",
                scope.handoff_id,
                scope.verification_id,
                None,
                None,
            ),
        )
        messages.append(
            {
                "role": "assistant",
                "content": first.content,
                "tool_calls": [
                    {
                        "id": call.call_id,
                        "type": "function",
                        "function": {"name": call.name, "arguments": call.arguments},
                    }
                ],
            }
        )
        messages.append(
            {"role": "tool", "tool_call_id": call.call_id, "content": _json(obs)}
        )
        if monotonic() - start > self.max_duration:
            raise AgentExecutionError("episode duration exceeded")
        second = self._complete(
            con, inv, messages, tool, start, response_protocol=response_protocol
        )
        if second.tool_calls or second.finish_reason == "length":
            raise AgentExecutionError("repeat tool call or truncated continuation")
        con.execute(
            "UPDATE tool_events SET observation_sent_turn=1,continuation_turn=2 "
            "WHERE tool_event_id=?",
            (event_id,),
        )
        return first, second, event_id, obs

    def _complete(
        self,
        con,
        inv: str,
        messages: list[dict],
        tool: str | None,
        start: float,
        response_protocol: str | None = None,
    ) -> ModelTurn:
        if monotonic() - start > self.max_duration:
            raise AgentExecutionError("episode duration exceeded")
        mode = (
            "JSON_SCHEMA_V2"
            if response_protocol == "V2_EXACT_QUOTE"
            else (
                "JSON_OBJECT"
                if messages[-1].get("role") == "tool" or tool is None
                else "NATIVE_TOOL"
            )
        )
        schema_sha256 = (
            sha256(_json(agent_a_v2_response_schema()).encode("utf-8")).hexdigest()
            if response_protocol == "V2_EXACT_QUOTE"
            else None
        )
        try:
            turn = self.transport.complete(
                messages,
                tool_name=tool,
                max_tokens=self.max_tokens,
                response_protocol=response_protocol,
            )
        except ProviderFailure as exc:
            evidence = json.loads(
                con.execute(
                    "SELECT turn_evidence_json FROM agent_invocations WHERE invocation_id=?",
                    (inv,),
                ).fetchone()[0]
            )
            evidence.append(
                {
                    "turn": len(evidence) + 1,
                    "requested_output_mode": mode,
                    "requested_schema_sha256": schema_sha256,
                    "transport_result": "SCHEMA_REJECTED"
                    if type(exc).__name__ == "SchemaRequestRejected"
                    else "FAILED",
                    "response_id": None,
                }
            )
            con.execute(
                "UPDATE agent_invocations SET turn_evidence_json=? WHERE invocation_id=?",
                (_json(evidence), inv),
            )
            raise
        if not isinstance(turn, ModelTurn):
            raise AgentExecutionError("provider returned invalid turn")
        evidence = json.loads(
            con.execute(
                "SELECT turn_evidence_json FROM agent_invocations WHERE invocation_id=?",
                (inv,),
            ).fetchone()[0]
        )
        evidence.append(
            {
                "turn": len(evidence) + 1,
                "response_id": turn.response_id,
                "requested_output_mode": mode,
                "transport_result": "RESPONSE_RECEIVED",
                "requested_schema_sha256": schema_sha256,
                "model_id": turn.model_id,
                "finish_reason": turn.finish_reason,
                "truncated": turn.finish_reason == "length",
                "usage": turn.usage,
                "native_tool_call_count": len(turn.tool_calls),
                "native_tool_call_ids": [call.call_id for call in turn.tool_calls],
                "content_length": len(turn.content)
                if turn.content is not None
                else None,
                "content_sha256": sha256(turn.content.encode("utf-8")).hexdigest()
                if turn.content is not None
                else None,
            }
        )
        con.execute(
            "UPDATE agent_invocations SET turn_evidence_json=? WHERE invocation_id=?",
            (_json(evidence), inv),
        )
        return turn

    def _mark_last(self, con, inv: str, **fields: str) -> None:
        evidence = json.loads(
            con.execute(
                "SELECT turn_evidence_json FROM agent_invocations WHERE invocation_id=?",
                (inv,),
            ).fetchone()[0]
        )
        if evidence:
            evidence[-1].update(fields)
            con.execute(
                "UPDATE agent_invocations SET turn_evidence_json=? WHERE invocation_id=?",
                (_json(evidence), inv),
            )

    def _finish(
        self,
        con,
        inv: str,
        first: ModelTurn,
        turn: ModelTurn | None,
        event: str | None,
        *,
        status: str = "COMPLETE",
    ) -> None:
        evidence = json.loads(
            con.execute(
                "SELECT turn_evidence_json FROM agent_invocations WHERE invocation_id=?",
                (inv,),
            ).fetchone()[0]
        )
        if evidence:
            evidence[0]["tool_event_id"] = event
        con.execute(
            "UPDATE agent_invocations SET status=?,ended_at=?,"
            "turn_evidence_json=? WHERE invocation_id=?",
            (status, _now(), _json(evidence), inv),
        )
