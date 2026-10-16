"""Freeze Task 04A deterministic previews; no admission or approval decision.

LIVE material persistence must later require validated genuine B proposal evidence.
Only this narrow test-mode path is available before the agent/runtime milestone.
"""

from datetime import datetime, timezone
import json
import sqlite3

from scoperelay.storage.transaction import immediate

from .contracts import CandidateRequestV1, TrustedTask
from .renderer import PreparedMaterial, render_escalation, render_routine


class PreviewStoreError(ValueError):
    pass


def _stamp(at: datetime) -> str:
    if at.tzinfo is None or at.utcoffset() != timezone.utc.utcoffset(at):
        raise PreviewStoreError("UTC timestamp required")
    return (
        at.astimezone(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _test_episode(con: sqlite3.Connection, task: TrustedTask) -> None:
    row = con.execute(
        "SELECT run_kind FROM episodes WHERE episode_id=?", (task.episode_id,)
    ).fetchone()
    if row is None or row[0] != "DETERMINISTIC_COMPONENT_TEST":
        raise PreviewStoreError(
            "Task 04A preview persistence is deterministic-test only"
        )


def _insert(
    con: sqlite3.Connection,
    material_id: str,
    task: TrustedTask,
    preview: PreparedMaterial,
    at: datetime,
) -> None:
    if not material_id or len(material_id) > 128:
        raise PreviewStoreError("invalid trusted material identity")
    payload = json.loads(preview.canonical_json)
    con.execute(
        "INSERT INTO material_actions (material_action_id,episode_id,action_id,business_key,"
        "canonical_schema_version,operation_class,org_id,grant_id,ticket_id,source_version,"
        "account_id,order_id,recipient_contact_id,field_values_json,template_id,rendered_body,"
        "binding_versions_json,canonical_json,material_sha256,created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            material_id,
            task.episode_id,
            preview.action_id,
            preview.business_key,
            1,
            preview.operation_class,
            payload["org_id"],
            payload["grant_id"],
            payload["ticket_id"],
            payload["source_version"],
            payload["account_id"],
            payload["order_id"],
            preview.recipient_contact_id,
            json.dumps(
                payload["field_values"],
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            preview.template_id,
            preview.rendered_body,
            preview.binding_versions_json,
            preview.canonical_json,
            preview.material_sha256,
            _stamp(at),
        ),
    )


def freeze_routine_test_preview(
    con: sqlite3.Connection,
    task: TrustedTask,
    candidate: CandidateRequestV1,
    *,
    at: datetime,
    material_action_id: str,
    action_id: str,
    purpose: str,
    template_id: str,
    selected_field_ids: tuple[str, ...],
) -> PreparedMaterial:
    with immediate(con):
        _test_episode(con, task)
        preview = render_routine(
            con,
            task,
            candidate,
            at=at,
            action_id=action_id,
            purpose=purpose,
            template_id=template_id,
            selected_field_ids=selected_field_ids,
        )
        _insert(con, material_action_id, task, preview, at)
    return preview


def freeze_escalation_test_preview(
    con: sqlite3.Connection,
    task: TrustedTask,
    candidate: CandidateRequestV1,
    *,
    at: datetime,
    material_action_id: str,
    action_id: str,
    approval_id: str,
    expires_at: datetime,
) -> PreparedMaterial:
    if expires_at <= at or not approval_id or len(approval_id) > 128:
        raise PreviewStoreError("invalid pending approval expiry or identity")
    with immediate(con):
        _test_episode(con, task)
        preview = render_escalation(con, task, candidate, at=at, action_id=action_id)
        _insert(con, material_action_id, task, preview, at)
        con.execute(
            "INSERT INTO escalation_approvals (approval_id,material_action_id,material_sha256,"
            "supervisor_id,supervisor_version,decision,decided_at,status,approved_at,expires_at,"
            "revoked_at,consumed_effect_id,version) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                approval_id,
                material_action_id,
                preview.material_sha256,
                None,
                None,
                None,
                None,
                "PENDING",
                None,
                _stamp(expires_at),
                None,
                None,
                1,
            ),
        )
    return preview
