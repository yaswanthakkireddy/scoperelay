"""Fixed, read-only projections for synthetic reviewer pages.

No browser parameter becomes a table name, SQL fragment, filesystem path, or
trusted principal. Evaluator-only truth is deliberately absent from every SQL.
"""

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
import json
import sqlite3
from hashlib import sha256

from scoperelay.storage.writer_owner import quarantine_path


DECISIONS = ("ALLOW", "DENY", "CLARIFY", "REQUEST APPROVAL", "INCOMPLETE EVIDENCE")
RESOURCE_OUTCOMES = (
    "NOT_ATTEMPTED",
    "ACCEPTED_COMMITTED",
    "EXPLICIT_POLICY_DENIAL",
    "TECHNICAL_ERROR",
    "INDETERMINATE",
    "RECONCILED_COMMITTED",
    "RECONCILED_NOT_COMMITTED",
)
_PUBLIC_QUARANTINE_REASONS = frozenset(
    {
        "CONTROLLED_RESTORE_REVIEW_REQUIRED",
        "MISSING_CONTINUITY_WITH_HISTORY",
        "INVALID_CONTINUITY",
        "UNMATCHED_COMMIT_INTENT",
        "ACCEPTED_EFFECT_WITHOUT_INTENT",
        "ACCEPTED_EFFECT_EVIDENCE_INCONSISTENT",
    }
)
_OFFLINE_EXPORT = (
    Path(__file__).resolve().parents[3] / "showcase" / "public" / "data.json"
)


def offline_evaluation(path: Path) -> dict:
    """Read a fixed, public, offline export; never treat it as a current DB decision."""
    if not _OFFLINE_EXPORT.is_file():
        return {"available": False, "cases": []}
    try:
        payload = json.loads(_OFFLINE_EXPORT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"available": False, "cases": []}
    if (
        payload.get("schema") != "scoperelay-public-offline-v1"
        or payload.get("mode") != "DETERMINISTIC_COMPONENT_TEST"
        or payload.get("model_generations") != 0
        or not isinstance(payload.get("cases"), list)
        or len(payload["cases"]) != 8
    ):
        return {"available": False, "cases": []}
    current = {row["scenario_id"]: row for row in scenarios(path)}
    cases = []
    for row in payload["cases"]:
        if not isinstance(row, dict) or not all(
            key in row
            for key in (
                "scenario_id",
                "run_kind",
                "model_invoked",
                "judgment",
                "decision",
                "b_status",
                "quote_binding",
                "quote_binding_diagnostic",
                "failure_stage",
                "protected_state_unchanged",
                "resource_attempts",
                "queue_effects",
                "ticket_id",
                "source_version",
                "source_sha256",
            )
        ):
            return {"available": False, "cases": []}
        if (
            row["run_kind"] != "DETERMINISTIC_COMPONENT_TEST"
            or row["model_invoked"] is not False
            or row["protected_state_unchanged"] is not True
            or row["resource_attempts"] != 0
            or row["queue_effects"] != 0
        ):
            return {"available": False, "cases": []}
        scenario = current.get(row.get("scenario_id"))
        if (
            scenario is None
            or scenario["ticket_id"] != row.get("ticket_id")
            or (
                scenario["source_version"] != row.get("source_version")
                or sha256(scenario["body_text"].encode("utf-8")).hexdigest()
                != row.get("source_sha256")
            )
        ):
            continue
        cases.append(
            {
                key: row[key]
                for key in (
                    "scenario_id",
                    "run_kind",
                    "judgment",
                    "decision",
                    "b_status",
                    "quote_binding",
                    "quote_binding_diagnostic",
                    "failure_stage",
                    "protected_state_unchanged",
                    "resource_attempts",
                    "queue_effects",
                )
            }
        )
    if len({row["scenario_id"] for row in cases}) != len(cases):
        return {"available": False, "cases": []}
    return {"available": True, "cases": cases}


@contextmanager
def _reader(path: Path) -> Iterator[sqlite3.Connection]:
    if not path.is_file():
        raise FileNotFoundError("authoritative synthetic database is absent")
    con = sqlite3.connect(
        path.as_uri() + "?mode=ro", uri=True, autocommit=True, timeout=2
    )
    try:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA query_only=ON")
        yield con
    finally:
        con.close()


def _one(con: sqlite3.Connection, sql: str, args: tuple = ()) -> dict | None:
    row = con.execute(sql, args).fetchone()
    return dict(row) if row is not None else None


def _many(con: sqlite3.Connection, sql: str, args: tuple = ()) -> list[dict]:
    return [dict(row) for row in con.execute(sql, args)]


def _unpack(row: dict | None, *fields: str) -> dict | None:
    if row is not None:
        for field in fields:
            try:
                row[field] = (
                    json.loads(row[field]) if row.get(field) is not None else None
                )
            except (ValueError, TypeError):
                row[field] = {"evidence_error": "Stored JSON unavailable"}
    return row


def overview(path: Path) -> dict:
    with _reader(path) as con:
        identity = _one(
            con,
            "SELECT identity_uuid,created_at FROM database_identity WHERE singleton=1",
        )
        counts = {
            name: con.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
            for name in (
                "scenarios",
                "episodes",
                "agent_invocations",
                "resource_attempts",
                "queue_effects",
                "escalation_approvals",
                "original_actions",
            )
        }
        modes = _many(
            con,
            "SELECT run_kind,count(*) AS count FROM episodes GROUP BY run_kind ORDER BY run_kind",
        )
        live_invocations = con.execute(
            "SELECT count(*) FROM agent_invocations WHERE is_live=1"
        ).fetchone()[0]
        scenarios = _many(
            con, "SELECT scenario_id,name FROM scenarios ORDER BY scenario_id"
        )
    held = quarantine_path(path).exists()
    return {
        "identity": identity,
        "counts": counts,
        "modes": modes,
        "live_invocations": live_invocations,
        "scenarios": scenarios,
        "quarantined": held,
        "admission": "DISABLED_IN_WORKBENCH" if not held else "QUARANTINED",
    }


def scenarios(path: Path) -> list[dict]:
    with _reader(path) as con:
        return _many(
            con,
            "SELECT s.scenario_id,s.name,s.fixture_version,s.fixture_epoch,"
            "t.ticket_id,ts.body_text,ts.source_version,ts.body_sha256,"
            "(SELECT count(*) FROM episodes e WHERE e.scenario_id=s.scenario_id) AS episode_count "
            "FROM scenarios s JOIN tickets t ON t.ticket_id=json_extract(s.manifest_json,'$.ticket_id') "
            "JOIN ticket_sources ts ON ts.ticket_id=t.ticket_id "
            "AND ts.source_version=t.current_source_version ORDER BY s.scenario_id",
        )


def scenario(path: Path, scenario_id: str) -> dict | None:
    with _reader(path) as con:
        record = _one(
            con,
            "SELECT s.scenario_id,s.name,s.fixture_version,s.fixture_epoch,"
            "t.ticket_id,t.org_id,t.customer_id,t.assignment_version,t.current_source_version,"
            "ts.source_id,ts.source_version,ts.body_text,ts.body_sha256,ts.business_interaction_id,"
            "g.grant_id,g.manager_id,g.operation_class,g.version AS grant_version,"
            "g.expires_at,g.revoked_at,c.display_name AS customer_name,o.name AS org_name "
            "FROM scenarios s JOIN tickets t ON t.ticket_id=json_extract(s.manifest_json,'$.ticket_id') "
            "JOIN ticket_sources ts ON ts.ticket_id=t.ticket_id AND ts.source_version=t.current_source_version "
            "LEFT JOIN manager_grants g ON g.grant_id=t.assigned_grant_id "
            "JOIN customers c ON c.customer_id=t.customer_id "
            "JOIN organizations o ON o.org_id=t.org_id WHERE s.scenario_id=?",
            (scenario_id,),
        )
        if record is None:
            return None
        record["orders"] = _many(
            con,
            "SELECT o.order_id,o.order_status,o.customer_visible_eta,o.version,"
            "a.account_id FROM orders o JOIN accounts a ON a.account_id=o.account_id "
            "WHERE a.customer_id=? AND a.org_id=? ORDER BY o.order_id",
            (record["customer_id"], record["org_id"]),
        )
        record["contacts"] = _many(
            con,
            "SELECT contact_id,channel,address,verified,active,version "
            "FROM contacts WHERE customer_id=? AND org_id=? ORDER BY contact_id",
            (record["customer_id"], record["org_id"]),
        )
        record["episodes"] = _many(
            con,
            "SELECT episode_id,run_kind,workflow_state,started_at "
            "FROM episodes WHERE scenario_id=? ORDER BY started_at DESC,episode_id",
            (scenario_id,),
        )
        return record


def episodes(path: Path) -> list[dict]:
    with _reader(path) as con:
        return _many(
            con,
            "SELECT e.episode_id,e.scenario_id,e.run_kind,e.workflow_state,e.started_at,"
            "e.finished_at,e.ticket_id,"
            "(SELECT count(*) FROM agent_invocations i WHERE i.episode_id=e.episode_id) AS invocation_count,"
            "(SELECT count(*) FROM queue_effects q JOIN material_actions m "
            "ON m.material_action_id=q.material_action_id WHERE m.episode_id=e.episode_id) AS effect_count "
            "FROM episodes e ORDER BY e.started_at DESC,e.episode_id LIMIT 200",
        )


def episode(path: Path, episode_id: str) -> dict | None:
    with _reader(path) as con:
        record = _one(
            con,
            "SELECT e.episode_id,e.scenario_id,e.fixture_epoch,e.ticket_id,e.source_version,"
            "e.grant_id,e.run_kind,e.workflow_state,e.started_at,e.finished_at,"
            "ts.source_id,ts.body_text,ts.body_sha256 "
            "FROM episodes e JOIN ticket_sources ts ON ts.ticket_id=e.ticket_id "
            "AND ts.source_version=e.source_version WHERE e.episode_id=?",
            (episode_id,),
        )
        if record is None:
            return None
        record["grant"] = _one(
            con,
            "SELECT grant_id,org_id,manager_id,operation_class,scope_ticket_id,"
            "policy_version,version,expires_at,revoked_at FROM manager_grants WHERE grant_id=?",
            (record["grant_id"],),
        )
        record["invocations"] = _many(
            con,
            "SELECT invocation_id,agent_role,provider,model_id,is_live,status,"
            "started_at,ended_at,failure_code FROM agent_invocations "
            "WHERE episode_id=? ORDER BY started_at,invocation_id",
            (episode_id,),
        )
        for invocation in record["invocations"]:
            invocation["tools"] = _many(
                con,
                "SELECT tool_event_id,call_id,tool_name,tool_status,"
                "observation_owner,observed_at,observation_sent_turn,continuation_turn,"
                "handoff_id,verification_id FROM tool_events WHERE invocation_id=? "
                "ORDER BY observed_at,tool_event_id",
                (invocation["invocation_id"],),
            )
        record["candidates"] = _many(
            con,
            "SELECT candidate_id,invocation_id,operation_hint,order_ref_text,"
            "requested_recipient_text,content_class_hint,source_spans_json,uncertainty_json "
            "FROM candidate_requests WHERE episode_id=? ORDER BY candidate_id",
            (episode_id,),
        )
        for candidate in record["candidates"]:
            _unpack(candidate, "source_spans_json", "uncertainty_json")
        record["candidate_protocol"] = _one(
            con,
            "SELECT protocol_version,pinned_at FROM episode_candidate_protocols WHERE episode_id=?",
            (episode_id,),
        ) or {"protocol_version": "UNPINNED", "pinned_at": None}
        record["quote_bindings"] = _many(
            con,
            "SELECT binding_id,invocation_id,tool_event_id,protocol_version,"
            "model_quotes_json,bound_spans_json,model_source_id,model_source_version,"
            "model_source_digest,binder_version,binding_status,relevance_status,"
            "diagnostic_code,candidate_id,created_at FROM candidate_quote_bindings "
            "WHERE episode_id=? ORDER BY created_at,binding_id",
            (episode_id,),
        )
        for binding in record["quote_bindings"]:
            _unpack(binding, "model_quotes_json", "bound_spans_json")
        record["verifications"] = _many(
            con,
            "SELECT verification_id,grant_id,ticket_id,customer_id,account_id,"
            "order_id,recipient_contact_id,versions_json,result_code,observed_at "
            "FROM verifications WHERE episode_id=? ORDER BY observed_at",
            (episode_id,),
        )
        for verification in record["verifications"]:
            _unpack(verification, "versions_json")
        record["decisions"] = _many(
            con,
            "SELECT decision_id,verification_id,decision,reason_code,decided_at "
            "FROM workflow_decisions WHERE episode_id=? ORDER BY decided_at",
            (episode_id,),
        )
        record["handoffs"] = _many(
            con,
            "SELECT handoff_id,validation_status,assembled_at,h1_grant_ref_json,"
            "h2_verified_refs_json,h3_candidate_json,h4_source_refs_json "
            "FROM handoffs WHERE episode_id=? ORDER BY assembled_at",
            (episode_id,),
        )
        for handoff in record["handoffs"]:
            _unpack(
                handoff,
                "h1_grant_ref_json",
                "h2_verified_refs_json",
                "h3_candidate_json",
                "h4_source_refs_json",
            )
        record["proposals"] = _many(
            con,
            "SELECT proposal_id,invocation_id,handoff_id,status,purpose,"
            "template_variant,selected_field_ids_json,observation_ref,nonmaterial_note,"
            "response_turn FROM agent_b_proposals WHERE episode_id=? ORDER BY proposal_id",
            (episode_id,),
        )
        for proposal in record["proposals"]:
            _unpack(proposal, "selected_field_ids_json")
        record["materials"] = _many(
            con,
            "SELECT m.material_action_id,m.action_id,m.business_key,m.operation_class,"
            "m.recipient_contact_id,c.address AS recipient_address,m.field_values_json,"
            "m.template_id,m.rendered_body,m.material_sha256,m.source_version,"
            "m.binding_versions_json,m.created_at "
            "FROM material_actions m JOIN contacts c ON c.contact_id=m.recipient_contact_id "
            "WHERE m.episode_id=? ORDER BY m.created_at",
            (episode_id,),
        )
        for material in record["materials"]:
            _unpack(material, "field_values_json", "binding_versions_json")
        record["attempts"] = _many(
            con,
            "SELECT attempt_id,action_id,business_key,material_sha256,"
            "submitted_at,outcome,reason_code,effect_id,resolution_at "
            "FROM resource_attempts WHERE episode_id=? ORDER BY submitted_at",
            (episode_id,),
        )
        record["effects"] = _many(
            con,
            "SELECT q.effect_id,q.action_id,q.business_key,q.material_action_id,"
            "q.recipient_contact_id,q.material_sha256,q.committed_at,"
            + _correlated_effect_sql()
            + " AS evidence_complete "
            "FROM queue_effects q JOIN material_actions m ON m.material_action_id=q.material_action_id "
            "WHERE m.episode_id=? ORDER BY q.committed_at",
            (episode_id,),
        )
        record["journal"] = _many(
            con,
            "SELECT j.event_seq,j.attempt_id,j.action_id,j.event_type,"
            "j.reason_code,j.effect_id,j.occurred_at FROM journal_events j "
            "JOIN resource_attempts a ON a.attempt_id=j.attempt_id "
            "WHERE a.episode_id=? ORDER BY j.event_seq",
            (episode_id,),
        )
        return record


def _correlated_effect_sql() -> str:
    """A queue row alone is not a complete accepted-attempt/journal witness."""
    return (
        "EXISTS(SELECT 1 FROM resource_attempts a JOIN journal_events j "
        "ON j.attempt_id=a.attempt_id WHERE a.action_id=q.action_id "
        "AND a.business_key=q.business_key AND a.material_sha256=q.material_sha256 "
        "AND a.effect_id=q.effect_id AND a.outcome='ACCEPTED_COMMITTED' "
        "AND j.action_id=q.action_id AND j.effect_id=q.effect_id "
        "AND j.event_type='ACCEPTED_COMMITTED')"
    )


def resource_history(path: Path) -> dict:
    with _reader(path) as con:
        materials = _many(
            con,
            "SELECT m.material_action_id,m.episode_id,m.action_id,m.business_key,"
            "m.operation_class,m.template_id,m.material_sha256,m.rendered_body,"
            "c.address AS recipient_address FROM material_actions m "
            "JOIN contacts c ON c.contact_id=m.recipient_contact_id "
            "ORDER BY m.created_at DESC,m.material_action_id LIMIT 200",
        )
        attempts = _many(
            con,
            "SELECT a.attempt_id,a.episode_id,a.action_id,a.business_key,a.outcome,"
            "a.reason_code,a.effect_id,a.submitted_at FROM resource_attempts a "
            "ORDER BY a.submitted_at DESC,a.attempt_id LIMIT 200",
        )
        effects = _many(
            con,
            "SELECT q.effect_id,q.action_id,q.business_key,q.material_action_id,"
            "q.recipient_contact_id,q.material_sha256,q.committed_at,q.body_text,"
            "c.address AS recipient_address,"
            + _correlated_effect_sql()
            + " AS evidence_complete "
            "FROM queue_effects q "
            "JOIN contacts c ON c.contact_id=q.recipient_contact_id "
            "ORDER BY q.committed_at DESC,q.effect_id LIMIT 200",
        )
        journal = _many(
            con,
            "SELECT event_seq,attempt_id,action_id,event_type,reason_code,effect_id,"
            "occurred_at FROM journal_events ORDER BY event_seq DESC LIMIT 200",
        )
    return {
        "materials": materials,
        "attempts": attempts,
        "effects": effects,
        "journal": journal,
    }


def recovery_status(path: Path) -> dict:
    with _reader(path) as con:
        approvals = _many(
            con,
            "SELECT a.approval_id,a.material_action_id,a.material_sha256,a.supervisor_id,"
            "a.decision,a.decided_at,a.status,a.approved_at,a.expires_at,a.consumed_effect_id,"
            "a.version,m.rendered_body,m.recipient_contact_id,m.operation_class "
            "FROM escalation_approvals a JOIN material_actions m "
            "ON m.material_action_id=a.material_action_id ORDER BY a.expires_at DESC LIMIT 200",
        )
        originals = _many(
            con,
            "SELECT a.action_id,a.material_action_id,a.business_key,a.material_sha256,"
            "a.operation_class,a.fixture_epoch,a.state,a.registered_at,a.dispatched_at,a.closed_at,"
            "CASE WHEN q.effect_id IS NOT NULL AND EXISTS ("
            "SELECT 1 FROM resource_attempts t JOIN journal_events j ON j.attempt_id=t.attempt_id "
            "WHERE t.action_id=a.action_id AND t.business_key=a.business_key "
            "AND t.effect_id=q.effect_id AND t.outcome='ACCEPTED_COMMITTED' "
            "AND j.effect_id=q.effect_id AND j.event_type='ACCEPTED_COMMITTED') "
            "THEN 'ACCEPTED_COMMITTED' WHEN q.effect_id IS NOT NULL THEN 'EVIDENCE_INCONSISTENT' "
            "WHEN r.reconciliation_id IS NOT NULL THEN 'RECONCILED_NOT_COMMITTED' "
            "ELSE 'UNRESOLVED_HISTORY' END AS evidence_state "
            "FROM original_actions a LEFT JOIN queue_effects q ON q.action_id=a.action_id "
            "LEFT JOIN reconciliation_events r ON r.action_id=a.action_id "
            "ORDER BY a.registered_at DESC LIMIT 200",
        )
        fences = _many(
            con,
            "SELECT reconciliation_id,action_id,outcome,database_uuid,fixture_epoch,"
            "reconciled_at FROM reconciliation_events ORDER BY reconciled_at DESC LIMIT 200",
        )
    marker = quarantine_path(path)
    raw_reason = (
        marker.read_text(encoding="utf-8", errors="replace").strip()
        if marker.exists()
        else None
    )
    return {
        "approvals": approvals,
        "originals": originals,
        "fences": fences,
        "quarantined": marker.exists(),
        "quarantine_reason": (
            raw_reason
            if raw_reason in _PUBLIC_QUARANTINE_REASONS
            else "UNSPECIFIED_LOCAL_HOLD"
        )
        if raw_reason is not None
        else None,
    }
