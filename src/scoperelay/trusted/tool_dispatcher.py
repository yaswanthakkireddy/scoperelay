"""Only two model-exposed read tools; every scope comes from server context."""

import json
import sqlite3
from dataclasses import asdict, dataclass

from scoperelay.inference.adapter import ToolCall
from scoperelay.storage.agent_reads import AgentReadRepository, ScopeError


class ToolScopeDenied(ValueError):
    pass


@dataclass(frozen=True)
class ToolScope:
    role: str
    episode_id: str
    grant_id: str
    ticket_id: str
    handoff_id: str | None = None
    verification_id: str | None = None


def dispatch(
    con: sqlite3.Connection, scope: ToolScope, call: ToolCall
) -> dict[str, object]:
    expected = (
        "read_assigned_ticket" if scope.role == "A" else "read_verified_order_status"
    )
    key = "ticket_id" if scope.role == "A" else "verification_id"
    if call.name != expected or not call.call_id:
        raise ToolScopeDenied("unapproved tool")
    try:
        args = json.loads(call.arguments)
    except (TypeError, ValueError) as exc:
        raise ToolScopeDenied("invalid tool arguments") from exc
    bound = scope.ticket_id if scope.role == "A" else scope.verification_id
    if (
        type(args) is not dict
        or set(args) != {key}
        or type(args[key]) is not str
        or args[key] != bound
    ):
        raise ToolScopeDenied("tool argument outside server scope")
    repo = AgentReadRepository(con)
    try:
        if scope.role == "A":
            view = repo.assigned_ticket(
                ticket_id=scope.ticket_id,
                grant_id=scope.grant_id,
                episode_id=scope.episode_id,
            )
            if len(view.body_text.encode("utf-8")) > 8192:
                raise ToolScopeDenied("ticket exceeds tool size bound")
            return {
                "source_id": view.source_id,
                "ticket_id": view.ticket_id,
                "source_version": view.source_version,
                "source_digest": view.body_sha256,
                "body_text": view.body_text,
                "trust_origin": "CUSTOMER_UNTRUSTED",
            }
        if scope.role == "B" and scope.handoff_id and scope.verification_id:
            view = repo.verified_order_status(
                verification_id=scope.verification_id,
                episode_id=scope.episode_id,
                handoff_id=scope.handoff_id,
            )
            return {**asdict(view), "source_owner": "TRUSTED_ORDER_REPOSITORY"}
    except ScopeError as exc:
        raise ToolScopeDenied("trusted scope or current record rejected") from exc
    raise ToolScopeDenied("missing trusted B scope")
