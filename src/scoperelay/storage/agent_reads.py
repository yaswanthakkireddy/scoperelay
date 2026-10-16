"""Fixed data projections for future scoped read tools; no oracle or SQL selector."""

from dataclasses import dataclass
import sqlite3

from .source_spans import resolve_span


class ScopeError(ValueError):
    pass


@dataclass(frozen=True)
class TicketSourceView:
    source_id: str
    ticket_id: str
    source_version: int
    body_text: str
    body_sha256: str


@dataclass(frozen=True)
class VerifiedOrderView:
    verification_id: str
    order_id: str
    order_status: str
    customer_visible_eta: str | None
    order_version: int


class AgentReadRepository:
    """Caller supplies server-bound scope; model arguments cannot choose tables."""

    def __init__(self, con: sqlite3.Connection) -> None:
        self._con = con

    def assigned_ticket(
        self, *, ticket_id: str, grant_id: str, episode_id: str
    ) -> TicketSourceView:
        row = self._con.execute(
            "SELECT s.source_id,t.ticket_id,s.source_version,s.body_text,s.body_sha256 "
            "FROM tickets t JOIN manager_grants g ON g.grant_id=t.assigned_grant_id "
            "AND g.scope_ticket_id=t.ticket_id AND g.org_id=t.org_id "
            "JOIN principals p ON p.principal_id=g.manager_id AND p.org_id=g.org_id "
            "AND p.role='MANAGER' AND p.active=1 "
            "JOIN organizations org ON org.org_id=t.org_id AND org.active=1 "
            "AND org.policy_version=g.policy_version "
            "JOIN episodes e ON e.ticket_id=t.ticket_id AND e.grant_id=g.grant_id "
            "JOIN ticket_sources s ON s.ticket_id=t.ticket_id "
            "AND s.source_version=t.current_source_version "
            "WHERE t.ticket_id=? AND g.grant_id=? AND e.episode_id=? "
            "AND e.source_version=s.source_version AND t.active=1 AND g.revoked_at IS NULL "
            "AND g.expires_at>strftime('%Y-%m-%dT%H:%M:%fZ','now')",
            (ticket_id, grant_id, episode_id),
        ).fetchone()
        if row is None:
            raise ScopeError("ticket is not in assigned grant scope")
        return TicketSourceView(*row)

    def source_excerpt(
        self,
        *,
        ticket_id: str,
        grant_id: str,
        episode_id: str,
        source_version: int,
        start: int,
        end: int,
        excerpt: str,
    ) -> str:
        view = self.assigned_ticket(
            ticket_id=ticket_id, grant_id=grant_id, episode_id=episode_id
        )
        return resolve_span(
            expected_ticket_id=view.ticket_id,
            expected_source_version=view.source_version,
            ticket_id=ticket_id,
            source_version=source_version,
            body_text=view.body_text,
            body_sha256=view.body_sha256,
            start=start,
            end=end,
            excerpt=excerpt,
        )

    def verified_order_status(
        self, *, verification_id: str, episode_id: str, handoff_id: str
    ) -> VerifiedOrderView:
        row = self._con.execute(
            "SELECT v.verification_id,o.order_id,o.order_status,o.customer_visible_eta,o.version "
            "FROM verifications v JOIN tickets t ON t.ticket_id=v.ticket_id "
            "JOIN episodes e ON e.episode_id=v.episode_id AND e.ticket_id=t.ticket_id "
            "AND e.grant_id=v.grant_id AND e.source_version=t.current_source_version "
            "JOIN manager_grants g ON g.grant_id=v.grant_id AND g.scope_ticket_id=t.ticket_id "
            "AND g.org_id=t.org_id AND g.revoked_at IS NULL "
            "AND g.expires_at>strftime('%Y-%m-%dT%H:%M:%fZ','now') "
            "JOIN organizations org ON org.org_id=t.org_id AND org.active=1 "
            "AND org.policy_version=g.policy_version "
            "JOIN handoffs h ON h.handoff_id=? AND h.episode_id=v.episode_id "
            "AND json_extract(h.h2_verified_refs_json,'$.verification_id')=v.verification_id "
            "AND json_extract(h.h1_grant_ref_json,'$.grant_id')=v.grant_id "
            "AND json_extract(h.h1_grant_ref_json,'$.grant_version')=g.version "
            "JOIN accounts a ON a.account_id=v.account_id AND a.customer_id=t.customer_id "
            "AND a.org_id=t.org_id AND a.active=1 "
            "JOIN orders o ON o.order_id=v.order_id AND o.account_id=a.account_id "
            "AND o.org_id=t.org_id AND o.active=1 "
            "JOIN contacts c ON c.contact_id=v.recipient_contact_id "
            "AND c.customer_id=t.customer_id AND c.org_id=t.org_id AND c.verified=1 AND c.active=1 "
            "WHERE v.verification_id=? AND v.episode_id=? AND v.result_code='VERIFIED' "
            "AND v.customer_id=t.customer_id AND v.grant_id=t.assigned_grant_id "
            "AND t.active=1 AND t.assigned_grant_id=g.grant_id "
            "AND t.assignment_version=json_extract(h.h1_grant_ref_json,'$.assignment_version') "
            "AND o.version=json_extract(v.versions_json,'$.order_version') "
            "AND c.version=json_extract(v.versions_json,'$.recipient_version')",
            (handoff_id, verification_id, episode_id),
        ).fetchone()
        if row is None:
            raise ScopeError("verification does not permit this order read")
        return VerifiedOrderView(*row)
