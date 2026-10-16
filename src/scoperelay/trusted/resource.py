"""API-108: sole application-owned synthetic protected-effect writer.

No model, browser or generic repository receives this object as a tool. SQLite
file administrators are outside the modular-monolith threat boundary.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from scoperelay.storage.database import open_existing
from scoperelay.storage.continuity import (
    ContinuityError,
    assert_no_commit_intent,
    prepare_continuity,
    record_commit_intent,
)
from scoperelay.storage.fixture_loader import business_key
from scoperelay.storage.source_spans import source_digest
from scoperelay.storage.writer_owner import (
    ApplicationWriter,
    HistoryQuarantined,
    OwnerInvalid,
    WriterBusy,
)

from .authorization import INTERNAL_FIELDS, OPS, REASONS, ROUTINE_FIELDS, _catalog, _utc
from .contracts import VerifiedFacts
from .renderer import (
    MaterialError,
    PreparedMaterial,
    _render_escalation,
    _render_routine,
    canonical_bytes,
)


class ResourceInputError(ValueError):
    """Malformed caller reference, not an authoritative protected denial."""


class ResourceEvidenceError(RuntimeError):
    """Stored purported receipt lacks the required matching authoritative rows."""


class _PolicyViolation(Exception):
    def __init__(self, code: str) -> None:
        self.code = code


@dataclass(frozen=True)
class ResourceCallerContext:
    """Created by trusted coordinator, never deserialized from model/browser JSON."""

    episode_id: str
    fixture_epoch: str


@dataclass(frozen=True)
class OriginalActionIdentity:
    """Captured before dispatch; bind recovery to the original DB file and material."""

    database_uuid: str
    file_device: int
    file_inode: int
    fixture_epoch: str
    episode_id: str
    action_id: str
    material_action_id: str
    business_key: str
    material_sha256: str
    operation_class: str


@dataclass(frozen=True)
class ResourceResultV1:
    outcome: str
    action_id: str
    attempt_id: str | None
    material_sha256: str | None
    effect_id: str | None
    journal_event_seq: int | None
    reason_code: str | None
    verified_at: str | None
    retry_guidance: str
    evidence_complete: bool
    original_effect_id: str | None
    fixture_epoch: str
    state_versions: dict[str, int | None]
    replayed: bool = False
    business_key: str | None = None
    material_action_id: str | None = None

    def __post_init__(self) -> None:
        if self.outcome not in {
            "NOT_ATTEMPTED",
            "ACCEPTED_COMMITTED",
            "EXPLICIT_POLICY_DENIAL",
            "TECHNICAL_ERROR",
            "INDETERMINATE",
            "RECONCILED_COMMITTED",
            "RECONCILED_NOT_COMMITTED",
        }:
            raise ValueError("invalid protected resource outcome")


def _checkpoint(stage: str) -> None:
    """No-op production boundary; tests may monkeypatch to simulate lost work/ack."""


def _stamp(at: datetime) -> str:
    if at.tzinfo is None or at.utcoffset() != timezone.utc.utcoffset(at):
        raise ResourceInputError("UTC admission clock required")
    return (
        at.astimezone(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _ref(value: str, name: str) -> None:
    if type(value) is not str or not value or len(value) > 128:
        raise ResourceInputError(f"invalid {name}")


def _accepted(
    con: sqlite3.Connection, action_id: str, epoch: str, *, replayed: bool
) -> ResourceResultV1:
    row = con.execute(
        "SELECT q.effect_id,q.action_id,q.business_key,q.material_sha256,q.material_action_id,"
        "q.recipient_contact_id,q.body_text,a.attempt_id,j.event_seq,m.canonical_json,m.rendered_body,"
        "m.recipient_contact_id AS material_recipient,m.business_key AS material_key,"
        "m.material_sha256 AS material_digest,m.operation_class,e.fixture_epoch "
        "FROM queue_effects q JOIN material_actions m ON m.material_action_id=q.material_action_id "
        "JOIN episodes e ON e.episode_id=m.episode_id "
        "JOIN resource_attempts a ON a.action_id=q.action_id AND a.effect_id=q.effect_id "
        "AND a.outcome='ACCEPTED_COMMITTED' "
        "JOIN journal_events j ON j.attempt_id=a.attempt_id AND j.effect_id=q.effect_id "
        "AND j.action_id=q.action_id AND j.event_type='ACCEPTED_COMMITTED' "
        "WHERE q.action_id=?",
        (action_id,),
    ).fetchall()
    if len(row) != 1:
        raise ResourceEvidenceError(
            "accepted effect lacks a unique attempt/journal witness"
        )
    r = row[0]
    try:
        digest = sha256(canonical_bytes(json.loads(r["canonical_json"]))).hexdigest()
    except (ValueError, MaterialError) as exc:
        raise ResourceEvidenceError("stored canonical material invalid") from exc
    if (
        r["fixture_epoch"] != epoch
        or r["material_sha256"] != digest
        or r["material_digest"] != digest
        or r["business_key"] != r["material_key"]
        or r["recipient_contact_id"] != r["material_recipient"]
        or r["body_text"] != r["rendered_body"]
    ):
        raise ResourceEvidenceError("accepted effect/material/epoch mismatch")
    if r["operation_class"] == "SANITIZED_INTERNAL_ESCALATION":
        approval = con.execute(
            "SELECT approval_id FROM escalation_approvals WHERE material_action_id=? "
            "AND material_sha256=? AND status='CONSUMED' AND decision='APPROVE' "
            "AND consumed_effect_id=?",
            (r["material_action_id"], digest, r["effect_id"]),
        ).fetchall()
        if len(approval) != 1:
            raise ResourceEvidenceError(
                "accepted escalation lacks consumed exact approval"
            )
    return ResourceResultV1(
        "ACCEPTED_COMMITTED",
        action_id,
        r["attempt_id"],
        digest,
        r["effect_id"],
        r["event_seq"],
        None,
        _stamp(datetime.now(timezone.utc)),
        "historical receipt; do not send again"
        if replayed
        else "committed to synthetic queue",
        True,
        r["effect_id"] if replayed else None,
        epoch,
        {},
        replayed,
        r["business_key"],
        r["material_action_id"],
    )


def _denial(
    con: sqlite3.Connection,
    *,
    action_id: str,
    business_key_value: str,
    digest: str,
    episode_id: str,
    epoch: str,
    reason: str,
    original_effect_id: str | None,
    stamp: str,
) -> ResourceResultV1:
    attempt_id = f"attempt-{uuid4().hex}"
    con.execute(
        "INSERT INTO resource_attempts (attempt_id,action_id,business_key,material_sha256,"
        "episode_id,submitted_at,outcome,reason_code,effect_id,resolution_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            attempt_id,
            action_id,
            business_key_value,
            digest,
            episode_id,
            stamp,
            "EXPLICIT_POLICY_DENIAL",
            reason,
            None,
            stamp,
        ),
    )
    _checkpoint("after_attempt")
    cursor = con.execute(
        "INSERT INTO journal_events (attempt_id,action_id,event_type,reason_code,effect_id,occurred_at,details_json) "
        "VALUES (?,?,?,?,?,?,?)",
        (attempt_id, action_id, "EXPLICIT_POLICY_DENIAL", reason, None, stamp, "{}"),
    )
    _checkpoint("after_journal")
    return ResourceResultV1(
        "EXPLICIT_POLICY_DENIAL",
        action_id,
        attempt_id,
        digest,
        None,
        cursor.lastrowid,
        reason,
        stamp,
        "do not retry unchanged material",
        True,
        original_effect_id,
        epoch,
        {},
    )


def _current_preview(
    con: sqlite3.Connection,
    m: sqlite3.Row,
    context: ResourceCallerContext,
    at: datetime,
) -> tuple[PreparedMaterial, dict[str, int | None]]:
    try:
        p = json.loads(m["canonical_json"])
        digest = sha256(canonical_bytes(p)).hexdigest()
    except (ValueError, MaterialError) as exc:
        raise _PolicyViolation("MATERIAL_CANONICAL_INVALID") from exc
    if digest != m["material_sha256"]:
        raise _PolicyViolation("MATERIAL_DIGEST_MISMATCH")
    if (
        p["schema_version"] != m["canonical_schema_version"]
        or p["action_id"] != m["action_id"]
        or p["business_key"] != m["business_key"]
        or p["operation_class"] != m["operation_class"]
        or p["org_id"] != m["org_id"]
        or p["grant_id"] != m["grant_id"]
        or p["ticket_id"] != m["ticket_id"]
        or p["source_version"] != m["source_version"]
        or p["account_id"] != m["account_id"]
        or p["order_id"] != m["order_id"]
        or p["recipient_contact_id"] != m["recipient_contact_id"]
        or p["template_id"] != m["template_id"]
        or p["rendered_body"] != m["rendered_body"]
        or p["field_values"] != json.loads(m["field_values_json"])
    ):
        raise _PolicyViolation("MATERIAL_COLUMNS_MISMATCH")
    episode = con.execute(
        "SELECT e.*,s.fixture_epoch AS scenario_epoch,t.org_id AS ticket_org,"
        "t.customer_id,t.assigned_grant_id,t.current_source_version,t.assignment_version,"
        "t.active AS ticket_active,o.active AS org_active,o.policy_version AS org_policy_version "
        "FROM episodes e JOIN scenarios s ON s.scenario_id=e.scenario_id "
        "JOIN tickets t ON t.ticket_id=e.ticket_id JOIN organizations o ON o.org_id=t.org_id "
        "WHERE e.episode_id=?",
        (m["episode_id"],),
    ).fetchone()
    if (
        episode is None
        or episode["episode_id"] != context.episode_id
        or episode["fixture_epoch"] != context.fixture_epoch
    ):
        raise _PolicyViolation("TRUSTED_CONTEXT_MISMATCH")
    if episode["run_kind"] != "DETERMINISTIC_COMPONENT_TEST":
        raise _PolicyViolation("LIVE_EVIDENCE_NOT_IMPLEMENTED")
    if (
        episode["scenario_epoch"] != context.fixture_epoch
        or p["fixture_epoch"] != context.fixture_epoch
        or not episode["org_active"]
        or not episode["ticket_active"]
        or episode["ticket_org"] != m["org_id"]
        or episode["ticket_id"] != m["ticket_id"]
        or episode["grant_id"] != m["grant_id"]
        or episode["assigned_grant_id"] != m["grant_id"]
        or episode["source_version"] != m["source_version"]
        or episode["current_source_version"] != m["source_version"]
        or episode["assignment_version"] != p["assignment_version"]
    ):
        raise _PolicyViolation("STALE_TASK_OR_ORG")
    source = con.execute(
        "SELECT * FROM ticket_sources WHERE ticket_id=? AND source_version=?",
        (m["ticket_id"], m["source_version"]),
    ).fetchone()
    if source is None or source_digest(source["body_text"]) != source["body_sha256"]:
        raise _PolicyViolation("SOURCE_INVALID")
    if (
        p["business_interaction_id"] != source["business_interaction_id"]
        or p["request_ordinal"] != 1
    ):
        raise _PolicyViolation("BUSINESS_INTERACTION_CHANGED")
    expected_key = business_key(
        org_id=episode["ticket_org"],
        interaction_id=source["business_interaction_id"],
        operation=m["operation_class"],
        epoch=context.fixture_epoch,
    )
    if expected_key != m["business_key"]:
        raise _PolicyViolation("BUSINESS_KEY_MISMATCH")
    policy = con.execute(
        "SELECT * FROM org_policy WHERE org_id=?", (m["org_id"],)
    ).fetchone()
    if policy is None:
        raise _PolicyViolation("POLICY_MISSING")
    permitted = _catalog(policy["permitted_ops_json"], OPS)
    routine_fields = _catalog(policy["routine_field_allowlist_json"], ROUTINE_FIELDS)
    internal_fields = _catalog(policy["internal_field_allowlist_json"], INTERNAL_FIELDS)
    reasons = _catalog(policy["escalation_reasons_json"], REASONS)
    if (
        policy["version"] != episode["org_policy_version"]
        or policy["version"] != p["policy_version"]
        or m["operation_class"] not in permitted
    ):
        raise _PolicyViolation("POLICY_CHANGED_OR_OPERATION_FORBIDDEN")
    grant = con.execute(
        "SELECT g.*,r.role,r.active AS manager_active,r.org_id AS manager_org "
        "FROM manager_grants g JOIN principals r ON r.principal_id=g.manager_id WHERE g.grant_id=?",
        (m["grant_id"],),
    ).fetchone()
    if (
        grant is None
        or grant["revoked_at"] is not None
        or _utc(grant["expires_at"]) <= at
    ):
        raise _PolicyViolation("GRANT_REVOKED_OR_EXPIRED")
    if (
        grant["org_id"] != m["org_id"]
        or grant["manager_org"] != m["org_id"]
        or grant["scope_ticket_id"] != m["ticket_id"]
        or grant["operation_class"] != m["operation_class"]
        or grant["version"] != p["grant_version"]
        or grant["policy_version"] != policy["version"]
        or grant["role"] != "MANAGER"
        or not grant["manager_active"]
    ):
        raise _PolicyViolation("GRANT_SCOPE_VERSION_OR_OPERATION_INVALID")
    _checkpoint("after_grant")
    recipient = con.execute(
        "SELECT * FROM contacts WHERE contact_id=?", (m["recipient_contact_id"],)
    ).fetchone()
    if (
        recipient is None
        or recipient["org_id"] != m["org_id"]
        or not recipient["active"]
        or not recipient["verified"]
        or recipient["version"] != p["recipient_version"]
        or recipient["channel"] != p["channel"]
    ):
        raise _PolicyViolation("RECIPIENT_CHANGED_OR_INVALID")
    operation = m["operation_class"]
    account_id = order_id = order_version = status = eta = reason = None
    if operation == "ROUTINE_ORDER_STATUS":
        order = con.execute(
            "SELECT o.*,a.customer_id AS owner_customer,a.org_id AS account_org,"
            "a.active AS account_active,c.active AS customer_active "
            "FROM orders o JOIN accounts a ON a.account_id=o.account_id "
            "JOIN customers c ON c.customer_id=a.customer_id WHERE o.order_id=?",
            (m["order_id"],),
        ).fetchone()
        if (
            order is None
            or order["org_id"] != m["org_id"]
            or order["account_org"] != m["org_id"]
            or order["owner_customer"] != episode["customer_id"]
            or not order["active"]
            or not order["account_active"]
            or not order["customer_active"]
            or recipient["customer_id"] != episode["customer_id"]
            or recipient["channel"] != "SYNTHETIC_CUSTOMER_QUEUE"
        ):
            raise _PolicyViolation("WRONG_OWNER_OR_RECIPIENT")
        if (
            order["account_id"] != m["account_id"]
            or order["version"] != p["order_version"]
        ):
            raise _PolicyViolation("ORDER_OWNERSHIP_OR_VERSION_CHANGED")
        account_id, order_id, order_version = (
            order["account_id"],
            order["order_id"],
            order["version"],
        )
        status, eta = order["order_status"], order["customer_visible_eta"]
    elif operation == "SANITIZED_INTERNAL_ESCALATION":
        if (
            recipient["contact_id"] != policy["internal_recipient_id"]
            or recipient["channel"] != "SYNTHETIC_INTERNAL_QUEUE"
            or m["account_id"] is not None
            or m["order_id"] is not None
        ):
            raise _PolicyViolation("INTERNAL_RECIPIENT_OR_SCOPE_INVALID")
        fields = p["field_values"]
        reason = next(
            (x["value"] for x in fields if x["field_id"] == "reason_category"), None
        )
        if reason not in reasons:
            raise _PolicyViolation("ESCALATION_REASON_INVALID")
    else:
        raise _PolicyViolation("UNSUPPORTED_OPERATION")
    _checkpoint("after_ownership")
    facts = VerifiedFacts(
        m["episode_id"],
        context.fixture_epoch,
        m["org_id"],
        policy["version"],
        m["grant_id"],
        grant["version"],
        m["ticket_id"],
        source["source_id"],
        m["source_version"],
        source["body_sha256"],
        episode["assignment_version"],
        source["business_interaction_id"],
        operation,
        episode["customer_id"],
        account_id,
        order_id,
        order_version,
        status,
        eta,
        recipient["contact_id"],
        recipient["version"],
        recipient["channel"],
        reason,
    )
    try:
        if operation == "ROUTINE_ORDER_STATUS":
            pairing = {
                "STATUS_T1": ("STATUS_BRIEF", ("order_id", "order_status")),
                "STATUS_T2": (
                    "STATUS_WITH_ETA",
                    ("order_id", "order_status", "customer_visible_eta"),
                ),
                "STATUS_NOETA_T1": ("STATUS_NO_ETA", ("order_id", "order_status")),
            }
            spec = pairing.get(m["template_id"])
            if spec is None or not set(spec[1]) <= routine_fields:
                raise _PolicyViolation("TEMPLATE_OR_FIELD_FORBIDDEN")
            expected = _render_routine(
                facts,
                action_id=m["action_id"],
                purpose=spec[0],
                template_id=m["template_id"],
                selected_field_ids=spec[1],
            )
        else:
            if not {"ticket_id", "reason_category", "summary_code"} <= internal_fields:
                raise _PolicyViolation("INTERNAL_FIELD_FORBIDDEN")
            expected = _render_escalation(facts, action_id=m["action_id"])
    except MaterialError as exc:
        raise _PolicyViolation("RENDERED_MATERIAL_INVALID") from exc
    if (
        expected.canonical_json != m["canonical_json"]
        or expected.material_sha256 != m["material_sha256"]
        or expected.rendered_body != m["rendered_body"]
        or expected.binding_versions_json != m["binding_versions_json"]
    ):
        raise _PolicyViolation("MATERIAL_NOT_CURRENT_OR_EXACT")
    _checkpoint("after_material")
    versions = {
        "grant": grant["version"],
        "policy": policy["version"],
        "assignment": episode["assignment_version"],
        "source": m["source_version"],
        "order": order_version,
        "recipient": recipient["version"],
    }
    return expected, versions


def _approval(
    con: sqlite3.Connection, m: sqlite3.Row, approval_id: str | None, at: datetime
) -> tuple[str, int] | None:
    if m["operation_class"] == "ROUTINE_ORDER_STATUS":
        if approval_id is not None:
            raise _PolicyViolation("ROUTINE_MUST_NOT_USE_APPROVAL")
        return None
    if approval_id is None:
        raise _PolicyViolation("APPROVAL_REQUIRED")
    row = con.execute(
        "SELECT a.*,p.role,p.active AS supervisor_active,p.org_id AS supervisor_org,"
        "p.version AS current_supervisor_version FROM escalation_approvals a "
        "LEFT JOIN principals p ON p.principal_id=a.supervisor_id WHERE a.approval_id=?",
        (approval_id,),
    ).fetchone()
    if (
        row is None
        or row["material_action_id"] != m["material_action_id"]
        or row["material_sha256"] != m["material_sha256"]
    ):
        raise _PolicyViolation("APPROVAL_SCOPE_OR_DIGEST_INVALID")
    if (
        row["status"] != "APPROVED"
        or row["decision"] != "APPROVE"
        or row["consumed_effect_id"] is not None
    ):
        raise _PolicyViolation("APPROVAL_NOT_APPROVED_OR_ALREADY_USED")
    if (
        _utc(row["expires_at"]) <= at
        or row["revoked_at"] is not None
        or row["role"] != "SUPERVISOR"
        or not row["supervisor_active"]
        or row["supervisor_org"] != m["org_id"]
        or row["current_supervisor_version"] != row["supervisor_version"]
    ):
        raise _PolicyViolation("APPROVAL_EXPIRED_OR_SUPERVISOR_INVALID")
    return row["approval_id"], row["version"]


def _register_original(
    con: sqlite3.Connection,
    path: Path,
    action_id: str,
    material_action_id: str,
    context: ResourceCallerContext,
    *,
    at: datetime,
) -> OriginalActionIdentity:
    m = con.execute(
        "SELECT m.*,e.fixture_epoch FROM material_actions m JOIN episodes e "
        "ON e.episode_id=m.episode_id WHERE m.material_action_id=?",
        (material_action_id,),
    ).fetchone()
    if (
        m is None
        or m["action_id"] != action_id
        or m["episode_id"] != context.episode_id
        or m["fixture_epoch"] != context.fixture_epoch
    ):
        raise ResourceInputError("original action identity mismatch")
    stat = path.stat()
    db = con.execute(
        "SELECT identity_uuid FROM database_identity WHERE singleton=1"
    ).fetchone()
    if db is None:
        raise ResourceEvidenceError("database identity missing")
    identity = OriginalActionIdentity(
        db[0],
        stat.st_dev,
        stat.st_ino,
        context.fixture_epoch,
        context.episode_id,
        action_id,
        material_action_id,
        m["business_key"],
        m["material_sha256"],
        m["operation_class"],
    )
    prepare_continuity(con, path, db[0])
    registered = con.execute(
        "SELECT * FROM original_actions WHERE action_id=?", (action_id,)
    ).fetchone()
    if registered is None:
        con.execute(
            "INSERT INTO original_actions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                action_id,
                material_action_id,
                m["business_key"],
                m["material_sha256"],
                m["operation_class"],
                context.episode_id,
                context.fixture_epoch,
                db[0],
                "REGISTERED",
                _stamp(at),
                None,
                None,
                None,
            ),
        )
    elif (
        registered["material_action_id"] != material_action_id
        or registered["business_key"] != m["business_key"]
        or registered["material_sha256"] != m["material_sha256"]
        or registered["operation_class"] != m["operation_class"]
        or registered["episode_id"] != context.episode_id
        or registered["fixture_epoch"] != context.fixture_epoch
        or registered["database_uuid"] != db[0]
    ):
        raise ResourceInputError("registered original action conflicts with material")
    return identity


class ProtectedCommunicationResource:
    """One local DB, one writer owner. No external delivery path exists."""

    def __init__(
        self, database_path: Path, owner: ApplicationWriter | None = None
    ) -> None:
        self._path = database_path.resolve(strict=False)
        self._owner = owner

    def _operation(self, *, recovery: bool = False):
        if type(self._owner) is not ApplicationWriter or self._owner.path != self._path:
            raise OwnerInvalid("active application writer owner required")
        return self._owner.operation(recovery=recovery)

    def load_original_identity(
        self, action_id: str, fixture_epoch: str
    ) -> OriginalActionIdentity:
        """Restart recovery from durable trusted registration; never create missing history."""
        _ref(action_id, "action_id")
        _ref(fixture_epoch, "fixture_epoch")
        with self._operation(recovery=True):
            con = open_existing(self._path)
            con.row_factory = sqlite3.Row
            try:
                row = con.execute(
                    "SELECT * FROM original_actions WHERE action_id=?", (action_id,)
                ).fetchone()
                db = con.execute(
                    "SELECT identity_uuid FROM database_identity WHERE singleton=1"
                ).fetchone()
                if (
                    row is None
                    or db is None
                    or row["fixture_epoch"] != fixture_epoch
                    or row["database_uuid"] != db[0]
                ):
                    raise ResourceEvidenceError(
                        "original registration/epoch/database missing"
                    )
                stat = self._path.stat()
                return OriginalActionIdentity(
                    db[0],
                    stat.st_dev,
                    stat.st_ino,
                    fixture_epoch,
                    row["episode_id"],
                    action_id,
                    row["material_action_id"],
                    row["business_key"],
                    row["material_sha256"],
                    row["operation_class"],
                )
            finally:
                con.close()

    def capture_original_identity(
        self,
        action_id: str,
        material_action_id: str,
        trusted_context: ResourceCallerContext,
    ) -> OriginalActionIdentity:
        """Trusted coordinator calls before API-108; never infer identity from a fresh DB."""
        _ref(action_id, "action_id")
        _ref(material_action_id, "material_action_id")
        if type(trusted_context) is not ResourceCallerContext:
            raise ResourceInputError("server-owned caller context required")
        with self._operation():
            con = open_existing(self._path)
            con.row_factory = sqlite3.Row
            try:
                con.execute("BEGIN IMMEDIATE")
                self._owner.verify_connection(con)
                identity = _register_original(
                    con,
                    self._path,
                    action_id,
                    material_action_id,
                    trusted_context,
                    at=datetime.now(timezone.utc),
                )
                con.execute("COMMIT")
                return identity
            except BaseException:
                if con.in_transaction:
                    con.execute("ROLLBACK")
                raise
            finally:
                con.close()

    def reconcile_action(self, original: OriginalActionIdentity) -> ResourceResultV1:
        """API-109 under exclusive OS ownership; terminal absence is durably fenced."""
        if type(original) is not OriginalActionIdentity:
            raise ResourceInputError("original trusted recovery identity required")
        try:
            with self._operation(recovery=True):
                return self._reconcile_owned(original)
        except (WriterBusy, OwnerInvalid, FileNotFoundError):
            return ResourceResultV1(
                "INDETERMINATE",
                original.action_id,
                None,
                original.material_sha256,
                None,
                None,
                "ORIGINAL_WRITER_OWNERSHIP_UNAVAILABLE",
                _stamp(datetime.now(timezone.utc)),
                "preserve original action; writer may still be active",
                False,
                None,
                original.fixture_epoch,
                {},
                False,
                original.business_key,
                original.material_action_id,
            )

    def _reconcile_owned(self, original: OriginalActionIdentity) -> ResourceResultV1:
        if type(self._owner) is not ApplicationWriter:
            raise OwnerInvalid("recovery owner required")
        self._owner.assert_operation(recovery=True)

        def unknown(code: str) -> ResourceResultV1:
            return ResourceResultV1(
                "INDETERMINATE",
                original.action_id,
                None,
                original.material_sha256,
                None,
                None,
                code,
                _stamp(datetime.now(timezone.utc)),
                "preserve original DB and action; do not issue replacement",
                False,
                None,
                original.fixture_epoch,
                {},
                False,
                original.business_key,
                original.material_action_id,
            )

        try:
            file_stat = self._path.stat()
            if (file_stat.st_dev, file_stat.st_ino) != (
                original.file_device,
                original.file_inode,
            ):
                return unknown("ORIGINAL_DATABASE_FILE_CHANGED")
            con = open_existing(self._path)
            reopened_stat = self._path.stat()
            if (reopened_stat.st_dev, reopened_stat.st_ino) != (
                original.file_device,
                original.file_inode,
            ):
                con.close()
                return unknown("ORIGINAL_DATABASE_FILE_CHANGED")
        except (OSError, RuntimeError, sqlite3.Error):
            return unknown("ORIGINAL_DATABASE_UNAVAILABLE")
        con.row_factory = sqlite3.Row
        try:
            con.execute("BEGIN IMMEDIATE")
            self._owner.verify_connection(con)
            db = con.execute(
                "SELECT identity_uuid FROM database_identity WHERE singleton=1"
            ).fetchone()
            if db is None or db[0] != original.database_uuid:
                return unknown("ORIGINAL_DATABASE_IDENTITY_MISMATCH")
            m = con.execute(
                "SELECT m.*,e.fixture_epoch FROM material_actions m JOIN episodes e "
                "ON e.episode_id=m.episode_id WHERE m.material_action_id=?",
                (original.material_action_id,),
            ).fetchone()
            if (
                m is None
                or m["fixture_epoch"] != original.fixture_epoch
                or m["episode_id"] != original.episode_id
                or m["action_id"] != original.action_id
                or m["business_key"] != original.business_key
                or m["material_sha256"] != original.material_sha256
                or m["operation_class"] != original.operation_class
            ):
                return unknown("ORIGINAL_MATERIAL_IDENTITY_MISMATCH")
            lifecycle = con.execute(
                "SELECT * FROM original_actions WHERE action_id=?",
                (original.action_id,),
            ).fetchone()
            if lifecycle is not None and (
                lifecycle["material_action_id"] != original.material_action_id
                or lifecycle["business_key"] != original.business_key
                or lifecycle["material_sha256"] != original.material_sha256
                or lifecycle["operation_class"] != original.operation_class
                or lifecycle["episode_id"] != original.episode_id
                or lifecycle["fixture_epoch"] != original.fixture_epoch
                or lifecycle["database_uuid"] != original.database_uuid
            ):
                return unknown("ORIGINAL_LIFECYCLE_IDENTITY_MISMATCH")
            q = con.execute(
                "SELECT action_id,business_key,material_action_id,material_sha256 "
                "FROM queue_effects WHERE action_id=? OR business_key=?",
                (original.action_id, original.business_key),
            ).fetchall()
            if not q:
                if lifecycle is None:
                    return unknown("ORIGINAL_WRITER_LIFECYCLE_UNTRACKED")
                contradiction = con.execute(
                    "SELECT 1 FROM journal_events j JOIN resource_attempts a ON a.attempt_id=j.attempt_id "
                    "WHERE (a.action_id=? OR a.business_key=?) AND "
                    "(j.event_type='ACCEPTED_COMMITTED' OR a.outcome='ACCEPTED_COMMITTED') LIMIT 1",
                    (original.action_id, original.business_key),
                ).fetchone()
                if contradiction is not None:
                    return unknown("CONTRADICTORY_ACCEPTANCE_EVIDENCE")
                try:
                    assert_no_commit_intent(
                        self._path,
                        original.database_uuid,
                        action_id=original.action_id,
                        business_key=original.business_key,
                    )
                except ContinuityError:
                    return unknown("ORIGINAL_HISTORY_CONTINUITY_UNPROVED")
                if lifecycle["state"] == "CLOSED_NOT_COMMITTED":
                    witness = con.execute(
                        "SELECT reconciliation_id FROM reconciliation_events WHERE action_id=? "
                        "AND outcome='RECONCILED_NOT_COMMITTED' AND database_uuid=? AND fixture_epoch=?",
                        (
                            original.action_id,
                            original.database_uuid,
                            original.fixture_epoch,
                        ),
                    ).fetchone()
                    if witness is None:
                        return unknown("TERMINAL_FENCE_WITNESS_MISSING")
                else:
                    con.execute(
                        "UPDATE original_actions SET state='CLOSED_NOT_COMMITTED',closed_at=? "
                        "WHERE action_id=? AND state IN ('REGISTERED','DISPATCHED')",
                        (_stamp(datetime.now(timezone.utc)), original.action_id),
                    )
                    con.execute(
                        "INSERT INTO reconciliation_events VALUES (?,?,?,?,?,?,?)",
                        (
                            f"reconciliation-{uuid4().hex}",
                            original.action_id,
                            "RECONCILED_NOT_COMMITTED",
                            original.database_uuid,
                            original.fixture_epoch,
                            _stamp(datetime.now(timezone.utc)),
                            "{}",
                        ),
                    )
                    try:
                        con.execute("COMMIT")
                        _checkpoint("after_terminal_fence_commit")
                    except Exception:
                        return unknown("TERMINAL_FENCE_ACK_UNCERTAIN")
                    # Commit acknowledgment is not assumed; verify the durable fence below.
                    con.execute("BEGIN IMMEDIATE")
                    witness = con.execute(
                        "SELECT reconciliation_id FROM reconciliation_events WHERE action_id=? "
                        "AND outcome='RECONCILED_NOT_COMMITTED' AND database_uuid=? AND fixture_epoch=?",
                        (
                            original.action_id,
                            original.database_uuid,
                            original.fixture_epoch,
                        ),
                    ).fetchone()
                    if witness is None:
                        return unknown("TERMINAL_FENCE_ACK_UNCERTAIN")
                return ResourceResultV1(
                    "RECONCILED_NOT_COMMITTED",
                    original.action_id,
                    None,
                    original.material_sha256,
                    None,
                    None,
                    "TERMINAL_FENCE_COMMITTED",
                    _stamp(datetime.now(timezone.utc)),
                    "original action and business identity terminally fenced; no resend",
                    True,
                    None,
                    original.fixture_epoch,
                    {},
                    False,
                    original.business_key,
                    original.material_action_id,
                )
            if (
                len(q) != 1
                or q[0]["action_id"] != original.action_id
                or q[0]["business_key"] != original.business_key
                or q[0]["material_action_id"] != original.material_action_id
                or q[0]["material_sha256"] != original.material_sha256
            ):
                return unknown("CONTRADICTORY_EFFECT_IDENTITY")
            try:
                receipt = _accepted(
                    con, original.action_id, original.fixture_epoch, replayed=True
                )
            except ResourceEvidenceError:
                return unknown("INCONSISTENT_ACCEPTED_EVIDENCE")
            return ResourceResultV1(
                "RECONCILED_COMMITTED",
                receipt.action_id,
                receipt.attempt_id,
                receipt.material_sha256,
                receipt.effect_id,
                receipt.journal_event_seq,
                None,
                receipt.verified_at,
                "original synthetic queue commit confirmed",
                True,
                receipt.effect_id,
                receipt.fixture_epoch,
                {},
                True,
                receipt.business_key,
                receipt.material_action_id,
            )
        except sqlite3.Error:
            return unknown("RECONCILIATION_READ_ERROR")
        finally:
            if con.in_transaction:
                con.execute("ROLLBACK")
            con.close()

    def accepted_receipt(
        self, action_id: str, trusted_context: ResourceCallerContext
    ) -> ResourceResultV1:
        """Read-only historical evidence; not API-109 absence reconciliation."""
        _ref(action_id, "action_id")
        if type(trusted_context) is not ResourceCallerContext:
            raise ResourceInputError("server-owned caller context required")
        con = open_existing(self._path)
        con.row_factory = sqlite3.Row
        try:
            row = con.execute(
                "SELECT e.episode_id,e.fixture_epoch FROM queue_effects q "
                "JOIN material_actions m ON m.material_action_id=q.material_action_id "
                "JOIN episodes e ON e.episode_id=m.episode_id WHERE q.action_id=?",
                (action_id,),
            ).fetchone()
            if (
                row is None
                or row["episode_id"] != trusted_context.episode_id
                or row["fixture_epoch"] != trusted_context.fixture_epoch
            ):
                raise ResourceEvidenceError(
                    "no accepted receipt in trusted episode/epoch"
                )
            return _accepted(
                con, action_id, trusted_context.fixture_epoch, replayed=True
            )
        finally:
            con.close()

    def admit_material_action(
        self,
        action_id: str,
        material_action_id: str,
        approval_id: str | None,
        trusted_context: ResourceCallerContext,
        *,
        at: datetime | None = None,
    ) -> ResourceResultV1:
        """API-108: all effect attempts pass the OS owner before SQLite admission."""
        _ref(action_id, "action_id")
        _ref(material_action_id, "material_action_id")
        if type(trusted_context) is not ResourceCallerContext:
            raise ResourceInputError("server-owned caller context required")
        moment = at or datetime.now(timezone.utc)
        try:
            with self._operation():
                con = open_existing(self._path)
                con.row_factory = sqlite3.Row
                try:
                    con.execute("BEGIN IMMEDIATE")
                    self._owner.verify_connection(con)
                    m = con.execute(
                        "SELECT action_id FROM material_actions WHERE material_action_id=?",
                        (material_action_id,),
                    ).fetchone()
                    if m is not None and m["action_id"] == action_id:
                        identity = _register_original(
                            con,
                            self._path,
                            action_id,
                            material_action_id,
                            trusted_context,
                            at=moment,
                        )
                        previous = con.execute(
                            "SELECT 1 FROM queue_effects WHERE action_id=? OR business_key=? "
                            "LIMIT 1",
                            (action_id, identity.business_key),
                        ).fetchone()
                        if previous is None:
                            try:
                                assert_no_commit_intent(
                                    self._path,
                                    identity.database_uuid,
                                    action_id=action_id,
                                    business_key=identity.business_key,
                                )
                            except ContinuityError:
                                con.execute("ROLLBACK")
                                return ResourceResultV1(
                                    "INDETERMINATE",
                                    action_id,
                                    None,
                                    identity.material_sha256,
                                    None,
                                    None,
                                    "ORIGINAL_HISTORY_CONTINUITY_UNPROVED",
                                    _stamp(moment),
                                    "preserve original action; do not reissue",
                                    False,
                                    None,
                                    trusted_context.fixture_epoch,
                                    {},
                                    False,
                                    identity.business_key,
                                    material_action_id,
                                )
                        con.execute(
                            "UPDATE original_actions SET state='DISPATCHED',last_writer_id=?,"
                            "dispatched_at=?,closed_at=NULL WHERE action_id=? "
                            "AND state<>'CLOSED_NOT_COMMITTED'",
                            (f"writer-{uuid4().hex}", _stamp(moment), action_id),
                        )
                    con.execute("COMMIT")
                except BaseException:
                    if con.in_transaction:
                        con.execute("ROLLBACK")
                    raise
                finally:
                    con.close()
                return self._admit_owned(
                    action_id, material_action_id, approval_id, trusted_context, at=at
                )
        except HistoryQuarantined:
            return ResourceResultV1(
                "INDETERMINATE",
                action_id,
                None,
                None,
                None,
                None,
                "RESTORE_HISTORY_QUARANTINED",
                _stamp(moment),
                "preserve original history; no new protected action",
                False,
                None,
                trusted_context.fixture_epoch,
                {},
            )
        except (
            WriterBusy,
            OwnerInvalid,
            FileNotFoundError,
            sqlite3.Error,
            ContinuityError,
        ) as exc:
            return ResourceResultV1(
                "TECHNICAL_ERROR",
                action_id,
                None,
                None,
                None,
                None,
                type(exc).__name__,
                _stamp(moment),
                "original writer unavailable; do not change action identity",
                False,
                None,
                trusted_context.fixture_epoch,
                {},
            )

    def _admit_owned(
        self,
        action_id: str,
        material_action_id: str,
        approval_id: str | None,
        trusted_context: ResourceCallerContext,
        *,
        at: datetime | None = None,
    ) -> ResourceResultV1:
        if type(self._owner) is not ApplicationWriter:
            raise OwnerInvalid("admission owner required")
        self._owner.assert_operation()
        _ref(action_id, "action_id")
        _ref(material_action_id, "material_action_id")
        if approval_id is not None:
            _ref(approval_id, "approval_id")
        if type(trusted_context) is not ResourceCallerContext:
            raise ResourceInputError("server-owned caller context required")
        _ref(trusted_context.episode_id, "episode_id")
        _ref(trusted_context.fixture_epoch, "fixture_epoch")
        moment = at or datetime.now(timezone.utc)
        stamp = _stamp(moment)
        try:
            con = open_existing(self._path)
        except (OSError, RuntimeError, sqlite3.Error) as exc:
            return ResourceResultV1(
                "TECHNICAL_ERROR",
                action_id,
                None,
                None,
                None,
                None,
                type(exc).__name__,
                stamp,
                "verify original database path; no alternate DB",
                False,
                None,
                trusted_context.fixture_epoch,
                {},
            )
        con.row_factory = sqlite3.Row
        begun = False
        commit_started = False
        result: ResourceResultV1 | None = None
        attempt_id: str | None = None
        material_business_key: str | None = None
        try:
            _checkpoint("before_begin")
            con.execute("BEGIN IMMEDIATE")
            begun = True
            self._owner.verify_connection(con)
            _checkpoint("after_begin")
            m = con.execute(
                "SELECT * FROM material_actions WHERE material_action_id=?",
                (material_action_id,),
            ).fetchone()
            if m is None:
                raise ResourceInputError("unknown material action")
            material_business_key = m["business_key"]
            if m["episode_id"] != trusted_context.episode_id:
                raise ResourceInputError("material not in trusted episode")
            prior = con.execute(
                "SELECT action_id FROM queue_effects WHERE action_id=?", (action_id,)
            ).fetchone()
            if prior is not None:
                original = con.execute(
                    "SELECT material_sha256,material_action_id FROM queue_effects "
                    "WHERE action_id=?",
                    (action_id,),
                ).fetchone()
                if (
                    m["action_id"] != action_id
                    or m["material_sha256"] != original["material_sha256"]
                    or m["material_action_id"] != original["material_action_id"]
                ):
                    violation = "ACTION_ID_CONFLICT"
                else:
                    receipt = _accepted(
                        con, action_id, trusted_context.fixture_epoch, replayed=True
                    )
                    con.execute("COMMIT")
                    begun = False
                    return receipt
            else:
                violation = None
            business_prior = con.execute(
                "SELECT effect_id,action_id FROM queue_effects WHERE business_key=?",
                (m["business_key"],),
            ).fetchone()
            if violation is None and business_prior is not None:
                violation = "BUSINESS_ALREADY_COMMITTED"
            original_effect = (
                business_prior["effect_id"] if business_prior is not None else None
            )
            if violation is None:
                lifecycle = con.execute(
                    "SELECT state FROM original_actions WHERE action_id=?", (action_id,)
                ).fetchone()
                if (
                    lifecycle is not None
                    and lifecycle["state"] == "CLOSED_NOT_COMMITTED"
                ):
                    violation = "ORIGINAL_ACTION_TERMINALLY_FENCED"
                elif (
                    con.execute(
                        "SELECT 1 FROM original_actions WHERE business_key=? "
                        "AND action_id<>? AND state='CLOSED_NOT_COMMITTED' LIMIT 1",
                        (m["business_key"], action_id),
                    ).fetchone()
                    is not None
                ):
                    violation = "BUSINESS_IDENTITY_TERMINALLY_FENCED"
            if violation is None and m["action_id"] != action_id:
                violation = "ACTION_MATERIAL_MISMATCH"
            versions: dict[str, int | None] = {}
            if violation is None:
                try:
                    _, versions = _current_preview(con, m, trusted_context, moment)
                    approval = _approval(con, m, approval_id, moment)
                except _PolicyViolation as exc:
                    violation = exc.code
            if violation is not None:
                result = _denial(
                    con,
                    action_id=action_id,
                    business_key_value=m["business_key"],
                    digest=m["material_sha256"],
                    episode_id=m["episode_id"],
                    epoch=trusted_context.fixture_epoch,
                    reason=violation,
                    original_effect_id=original_effect,
                    stamp=stamp,
                )
            else:
                attempt_id = f"attempt-{uuid4().hex}"
                effect_id = f"effect-{uuid4().hex}"
                con.execute(
                    "INSERT INTO resource_attempts (attempt_id,action_id,business_key,material_sha256,"
                    "episode_id,submitted_at,outcome,reason_code,effect_id,resolution_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        attempt_id,
                        action_id,
                        m["business_key"],
                        m["material_sha256"],
                        m["episode_id"],
                        stamp,
                        "NOT_ATTEMPTED",
                        None,
                        None,
                        None,
                    ),
                )
                _checkpoint("after_attempt")
                con.execute(
                    "INSERT INTO queue_effects (effect_id,action_id,business_key,material_action_id,"
                    "recipient_contact_id,material_sha256,body_text,committed_at) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (
                        effect_id,
                        action_id,
                        m["business_key"],
                        m["material_action_id"],
                        m["recipient_contact_id"],
                        m["material_sha256"],
                        m["rendered_body"],
                        stamp,
                    ),
                )
                _checkpoint("after_queue")
                if approval is not None:
                    changed = con.execute(
                        "UPDATE escalation_approvals SET status='CONSUMED',consumed_effect_id=?,version=version+1 "
                        "WHERE approval_id=? AND status='APPROVED' AND version=?",
                        (effect_id, approval[0], approval[1]),
                    ).rowcount
                    if changed != 1:
                        raise sqlite3.IntegrityError(
                            "approval changed during consumption"
                        )
                con.execute(
                    "UPDATE resource_attempts SET outcome='ACCEPTED_COMMITTED',effect_id=?,resolution_at=? "
                    "WHERE attempt_id=?",
                    (effect_id, stamp, attempt_id),
                )
                con.execute(
                    "INSERT INTO journal_events (attempt_id,action_id,event_type,reason_code,effect_id,"
                    "occurred_at,details_json) VALUES (?,?,?,?,?,?,?)",
                    (
                        attempt_id,
                        action_id,
                        "ACCEPTED_COMMITTED",
                        None,
                        effect_id,
                        stamp,
                        "{}",
                    ),
                )
                _checkpoint("after_journal")
                database_uuid = con.execute(
                    "SELECT identity_uuid FROM database_identity WHERE singleton=1"
                ).fetchone()[0]
                record_commit_intent(
                    self._path,
                    database_uuid,
                    action_id=action_id,
                    business_key=m["business_key"],
                    material_sha256=m["material_sha256"],
                )
                _checkpoint("after_commit_intent")
                result = None
            commit_started = True
            con.execute("COMMIT")
            begun = False
            _checkpoint("after_commit")
            if result is not None:
                witness = con.execute(
                    "SELECT j.event_seq FROM journal_events j JOIN resource_attempts a "
                    "ON a.attempt_id=j.attempt_id WHERE a.attempt_id=? AND j.event_seq=? "
                    "AND a.outcome='EXPLICIT_POLICY_DENIAL' AND j.event_type='EXPLICIT_POLICY_DENIAL' "
                    "AND j.reason_code=a.reason_code AND j.action_id=a.action_id",
                    (result.attempt_id, result.journal_event_seq),
                ).fetchone()
                if witness is None:
                    raise ResourceEvidenceError(
                        "denial lacks committed journal witness"
                    )
                return result
            receipt = _accepted(
                con, action_id, trusted_context.fixture_epoch, replayed=False
            )
            return ResourceResultV1(
                receipt.outcome,
                receipt.action_id,
                receipt.attempt_id,
                receipt.material_sha256,
                receipt.effect_id,
                receipt.journal_event_seq,
                None,
                receipt.verified_at,
                receipt.retry_guidance,
                True,
                None,
                receipt.fixture_epoch,
                versions,
            )
        except ResourceInputError:
            if begun:
                con.execute("ROLLBACK")
            raise
        except Exception as exc:
            if commit_started:
                return ResourceResultV1(
                    "INDETERMINATE",
                    action_id,
                    result.attempt_id if result is not None else attempt_id,
                    None,
                    None,
                    None,
                    "COMMIT_ACK_UNCERTAIN",
                    stamp,
                    "do not retry; reconcile original action",
                    False,
                    None,
                    trusted_context.fixture_epoch,
                    {},
                    False,
                    material_business_key,
                    material_action_id,
                )
            if begun:
                try:
                    con.execute("ROLLBACK")
                except sqlite3.Error:
                    return ResourceResultV1(
                        "INDETERMINATE",
                        action_id,
                        None,
                        None,
                        None,
                        None,
                        "ROLLBACK_UNCERTAIN",
                        stamp,
                        "do not retry; reconcile original action",
                        False,
                        None,
                        trusted_context.fixture_epoch,
                        {},
                        False,
                        material_business_key,
                        material_action_id,
                    )
            return ResourceResultV1(
                "TECHNICAL_ERROR",
                action_id,
                None,
                None,
                None,
                None,
                type(exc).__name__,
                stamp,
                "same-ID retry only after cause is resolved",
                False,
                None,
                trusted_context.fixture_epoch,
                {},
            )
        finally:
            con.close()
