"""Trusted local supervisor simulation; never expose session issuance as an HTTP route."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from pathlib import Path
from secrets import token_urlsafe
import sqlite3

from scoperelay.storage.database import open_existing
from scoperelay.storage.transaction import immediate

from .resource import (
    ResourceCallerContext,
    _PolicyViolation,
    _approval,
    _current_preview,
    _stamp,
)


class ApprovalError(ValueError):
    pass


@dataclass(frozen=True)
class ApprovalPreview:
    approval_id: str
    material_sha256: str
    operation_class: str
    recipient_contact_id: str
    rendered_body: str
    field_values_json: str
    expires_at: str
    version: int
    status: str


def read_approved_escalation_context(
    con: sqlite3.Connection,
    *,
    episode_id: str,
    material_action_id: str,
    approval_id: str,
    at: datetime,
) -> tuple[str, str]:
    """Read-only pre-B check of already frozen, independently approved material.

    Returns (material digest, trusted reason); never changes an approval or effect.
    Final admission must independently repeat the checks in its writer transaction.
    """
    row = con.execute(
        "SELECT m.*,e.fixture_epoch FROM material_actions m JOIN episodes e "
        "ON e.episode_id=m.episode_id WHERE m.material_action_id=?",
        (material_action_id,),
    ).fetchone()
    if (
        row is None
        or row["episode_id"] != episode_id
        or row["operation_class"] != "SANITIZED_INTERNAL_ESCALATION"
    ):
        raise ApprovalError("material outside trusted escalation episode")
    try:
        _current_preview(
            con, row, ResourceCallerContext(episode_id, row["fixture_epoch"]), at
        )
        _approval(con, row, approval_id, at)
    except _PolicyViolation as exc:
        raise ApprovalError(f"preapproved escalation invalid: {exc.code}") from exc
    import json

    fields = json.loads(row["field_values_json"])
    reason = next(
        (item["value"] for item in fields if item["field_id"] == "reason_category"),
        None,
    )
    if not isinstance(reason, str):
        raise ApprovalError("trusted escalation reason missing")
    return row["material_sha256"], reason


class LocalSupervisorService:
    """Internal service boundary. UI authentication/CSRF remains a later milestone."""

    def __init__(self, database_path: Path) -> None:
        self._path = database_path

    def issue_trusted_local_session(self, *, at: datetime) -> str:
        """Trusted bootstrap only: fixed fixture supervisor, not a caller-selected identity."""
        token = token_urlsafe(32)
        stamp = _stamp(at)
        expiry = _stamp(at + timedelta(minutes=15))
        con = open_existing(self._path)
        try:
            with immediate(con):
                p = con.execute(
                    "SELECT role,active FROM principals WHERE principal_id='supervisor-1'"
                ).fetchone()
                if p != ("SUPERVISOR", 1):
                    raise ApprovalError("trusted supervisor fixture unavailable")
                con.execute(
                    "INSERT INTO operator_sessions VALUES (?,?,?,?,?,?,?,?)",
                    (
                        sha256(token.encode()).hexdigest(),
                        "supervisor-1",
                        "SUPERVISOR",
                        sha256(token_urlsafe(32).encode()).hexdigest(),
                        stamp,
                        expiry,
                        None,
                        1,
                    ),
                )
            return token
        finally:
            con.close()

    @staticmethod
    def _principal(con: sqlite3.Connection, token: str, at: datetime) -> sqlite3.Row:
        if type(token) is not str or len(token) < 32:
            raise ApprovalError("invalid supervisor session")
        row = con.execute(
            "SELECT p.principal_id,p.org_id,p.version,p.role,p.active,s.role_context,s.expires_at,s.revoked_at "
            "FROM operator_sessions s JOIN principals p ON p.principal_id=s.principal_id "
            "WHERE s.session_id_hash=?",
            (sha256(token.encode()).hexdigest(),),
        ).fetchone()
        if (
            row is None
            or row["role"] != "SUPERVISOR"
            or row["role_context"] != "SUPERVISOR"
            or not row["active"]
            or row["revoked_at"] is not None
            or row["expires_at"] <= _stamp(at)
        ):
            raise ApprovalError("supervisor session not currently entitled")
        return row

    @staticmethod
    def _preview(
        con: sqlite3.Connection, approval_id: str, principal: sqlite3.Row, at: datetime
    ) -> ApprovalPreview:
        row = con.execute(
            "SELECT m.*,a.material_sha256 AS approval_digest,e.fixture_epoch,g.manager_id "
            "FROM escalation_approvals a JOIN material_actions m ON m.material_action_id=a.material_action_id "
            "JOIN episodes e ON e.episode_id=m.episode_id "
            "JOIN manager_grants g ON g.grant_id=m.grant_id WHERE a.approval_id=?",
            (approval_id,),
        ).fetchone()
        if (
            row is None
            or row["org_id"] != principal["org_id"]
            or row["manager_id"] == principal["principal_id"]
            or row["operation_class"] != "SANITIZED_INTERNAL_ESCALATION"
        ):
            raise ApprovalError("approval not in independent supervisor scope")
        # Explicit aliases avoid relying on duplicate sqlite3.Row column names.
        approval = con.execute(
            "SELECT material_sha256,status,expires_at,version FROM escalation_approvals WHERE approval_id=?",
            (approval_id,),
        ).fetchone()
        if (
            approval["material_sha256"] != row["material_sha256"]
            or row["approval_digest"] != row["material_sha256"]
        ):
            raise ApprovalError("approval digest mismatch")
        try:
            _current_preview(
                con,
                row,
                ResourceCallerContext(row["episode_id"], row["fixture_epoch"]),
                at,
            )
        except _PolicyViolation as exc:
            raise ApprovalError(f"material no longer eligible: {exc.code}") from exc
        return ApprovalPreview(
            approval_id,
            row["material_sha256"],
            row["operation_class"],
            row["recipient_contact_id"],
            row["rendered_body"],
            row["field_values_json"],
            approval["expires_at"],
            approval["version"],
            approval["status"],
        )

    def review(self, token: str, approval_id: str, *, at: datetime) -> ApprovalPreview:
        con = open_existing(self._path)
        con.row_factory = sqlite3.Row
        try:
            con.execute("BEGIN")
            principal = self._principal(con, token, at)
            return self._preview(con, approval_id, principal, at)
        finally:
            if con.in_transaction:
                con.execute("ROLLBACK")
            con.close()

    def decide(
        self,
        token: str,
        approval_id: str,
        *,
        expected_version: int,
        expected_digest: str,
        decision: str,
        at: datetime,
    ) -> ApprovalPreview:
        if decision not in ("APPROVE", "REJECT") or type(expected_version) is not int:
            raise ApprovalError("invalid decision shape")
        con = open_existing(self._path)
        con.row_factory = sqlite3.Row
        try:
            with immediate(con):
                principal = self._principal(con, token, at)
                preview = self._preview(con, approval_id, principal, at)
                if (
                    preview.status != "PENDING"
                    or preview.version != expected_version
                    or preview.material_sha256 != expected_digest
                    or preview.expires_at <= _stamp(at)
                ):
                    raise ApprovalError(
                        "approval state, digest, version or expiry mismatch"
                    )
                stamp = _stamp(at)
                changed = con.execute(
                    "UPDATE escalation_approvals SET supervisor_id=?,supervisor_version=?,decision=?,"
                    "decided_at=?,status=?,approved_at=?,version=version+1 "
                    "WHERE approval_id=? AND status='PENDING' AND version=?",
                    (
                        principal["principal_id"],
                        principal["version"],
                        decision,
                        stamp,
                        "APPROVED" if decision == "APPROVE" else "REJECTED",
                        stamp if decision == "APPROVE" else None,
                        approval_id,
                        expected_version,
                    ),
                ).rowcount
                if changed != 1:
                    raise ApprovalError("concurrent approval decision")
                return self._preview(con, approval_id, principal, at)
        finally:
            con.close()

    def revoke(
        self, token: str, approval_id: str, *, expected_version: int, at: datetime
    ) -> None:
        con = open_existing(self._path)
        con.row_factory = sqlite3.Row
        try:
            with immediate(con):
                principal = self._principal(con, token, at)
                preview = self._preview(con, approval_id, principal, at)
                if preview.status != "APPROVED" or preview.version != expected_version:
                    raise ApprovalError("approval not revocable at expected version")
                con.execute(
                    "UPDATE escalation_approvals SET status='REVOKED',revoked_at=?,version=version+1 "
                    "WHERE approval_id=? AND status='APPROVED' AND version=?",
                    (_stamp(at), approval_id, expected_version),
                )
        finally:
            con.close()
