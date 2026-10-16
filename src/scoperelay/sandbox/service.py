"""Single-host local sandbox coordinator. No protected-resource import or writer API.

The per-run databases and metadata store are disposable local prototype state. They
are not a distributed/public hosting store or a production recovery mechanism.
"""

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import secrets
import sqlite3
from time import time

from scoperelay.application.read_only_agents import (
    AgentExecutionError,
    ReadOnlyAgentRunner,
)
from scoperelay.inference.adapter import ProviderAdapter, ProviderFailure
from scoperelay.storage.database import open_existing
from scoperelay.storage.fixture_loader import EPOCH, seed_synthetic
from scoperelay.storage.schema import initialize
from scoperelay.storage.transaction import immediate

from .offline import (
    OfflineTransportRateLimit,
    OfflineTransportTimeout,
    SandboxOfflineTransport,
)
from .lease import SingleInstanceLease
from .storage import LocalSandboxState


ROOT = Path(__file__).resolve().parents[3]
SOURCES = ROOT / "fixtures/scenarios/source_texts.json"
GUIDED = tuple(f"SC-{i:02d}" for i in range(1, 9))
_RUN_NAME = re.compile(r"run-[0-9a-f]{32}\.db")
SESSION_TTL = 1800
MAX_RUNS_PER_SESSION = 5
MAX_OFFLINE_TURNS_PER_DAY = 40
MAX_SESSIONS_PER_DAY = 100
MAX_ACTIVE_SESSIONS = 20


class SandboxRejected(ValueError):
    def __init__(self, code: str, status: int = 400) -> None:
        super().__init__(code)
        self.code, self.status = code, status


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _stamp() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


class SandboxService:
    """Local explicit opt-in. All public-facing methods enforce session ownership."""

    def __init__(
        self,
        root: Path,
        *,
        enabled: bool = False,
        daily_turn_budget: int = MAX_OFFLINE_TURNS_PER_DAY,
        session_ttl: int = SESSION_TTL,
        startup_sweep: bool = True,
        beta_restricted: bool = False,
        local_live_enabled: bool = False,
    ) -> None:
        self.root = root.resolve()
        self.store = LocalSandboxState(self.root)
        self.enabled = enabled
        if daily_turn_budget < 0 or daily_turn_budget > MAX_OFFLINE_TURNS_PER_DAY:
            raise ValueError("invalid offline budget")
        if session_ttl < 60 or session_ttl > SESSION_TTL:
            raise ValueError("invalid session TTL")
        self.daily_turn_budget = daily_turn_budget
        self.session_ttl = session_ttl
        self.beta_restricted = beta_restricted
        self.local_live_enabled = local_live_enabled
        if local_live_enabled and not enabled:
            raise ValueError("local live requires enabled sandbox")
        if not enabled:
            return
        self.store.initialize()
        if startup_sweep:
            self.sweep_expired()

    def _connect(self) -> sqlite3.Connection:
        return self.store.connect()

    def _require_enabled(self) -> None:
        if not self.enabled:
            raise SandboxRejected("SANDBOX_DISABLED", 503)

    def disable_new_runs(self) -> None:
        """Trusted local operator-only kill switch; intentionally no HTTP route."""
        self._require_enabled()
        con = self._connect()
        try:
            con.execute("UPDATE control SET accept_new_runs=0 WHERE id=1")
        finally:
            con.close()

    def recover_interrupted_runs(self, lease: SingleInstanceLease) -> int:
        """Only after exclusive CLI startup; never refunds uncertain turn slots.

        The OS lock proves that the previous local service process is gone,
        not that a possible provider call was free or that its output existed.
        """
        self._require_enabled()
        if not lease.held or lease.root != self.root:
            raise ValueError("exclusive local sandbox lease required")
        con = self._connect()
        try:
            with immediate(con):
                count = con.execute(
                    "SELECT count(*) FROM runs WHERE status='RUNNING'"
                ).fetchone()[0]
                con.execute(
                    "UPDATE runs SET status='INTERRUPTED', "
                    "failure_code='WORKER_INTERRUPTED' WHERE status='RUNNING'"
                )
            return count
        finally:
            con.close()

    def sweep_expired(self) -> int:
        """Delete only service-named expired run files, never a caller-selected path."""
        self._require_enabled()
        con = self._connect()
        try:
            with immediate(con):
                cutoff = time()
                rows = con.execute(
                    "SELECT db_name FROM runs WHERE expires_at<=? AND status!='RUNNING'",
                    (cutoff,),
                ).fetchall()
                for row in rows:
                    path = self._run_path(row["db_name"])
                    for target in (
                        path,
                        Path(str(path) + "-wal"),
                        Path(str(path) + "-shm"),
                    ):
                        if target.is_symlink():
                            raise OSError("sandbox run path is a symlink")
                        if target.is_file():
                            target.unlink()
                con.execute(
                    "DELETE FROM runs WHERE expires_at<=? AND status!='RUNNING'",
                    (cutoff,),
                )
                con.execute(
                    "DELETE FROM sessions WHERE expires_at<=? AND NOT EXISTS "
                    "(SELECT 1 FROM runs WHERE runs.token_hash=sessions.token_hash)",
                    (cutoff,),
                )
                con.execute(
                    "DELETE FROM client_windows WHERE minute<?",
                    (int(cutoff // 60) - 60,),
                )
        finally:
            con.close()
        return len(rows)

    def new_session(self, client_key: str = "trusted-local") -> tuple[str, str]:
        self._require_enabled()
        self.sweep_expired()
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        con = self._connect()
        try:
            with immediate(con):
                day = datetime.now(timezone.utc).date().isoformat()
                created_row = con.execute(
                    "SELECT created_count FROM day_sessions WHERE day=?", (day,)
                ).fetchone()
                if created_row and created_row[0] >= MAX_SESSIONS_PER_DAY:
                    raise SandboxRejected("SESSION_CAPACITY_EXHAUSTED", 429)
                active = con.execute(
                    "SELECT count(*) FROM sessions WHERE expires_at>?", (time(),)
                ).fetchone()[0]
                if active >= MAX_ACTIVE_SESSIONS:
                    raise SandboxRejected("SESSION_CAPACITY_EXHAUSTED", 429)
                if not self.store.charge_client(
                    con,
                    client_key,
                    "session",
                    int(time() // 60),
                    3 if self.beta_restricted else 10,
                ):
                    raise SandboxRejected("CLIENT_SESSION_RATE_LIMIT", 429)
                con.execute(
                    "INSERT INTO sessions VALUES (?,?,?)",
                    (_digest(token), _digest(csrf), time() + self.session_ttl),
                )
                con.execute(
                    "INSERT INTO day_sessions(day,created_count) VALUES (?,1) "
                    "ON CONFLICT(day) DO UPDATE SET created_count=created_count+1",
                    (day,),
                )
        finally:
            con.close()
        return token, csrf

    def refresh_csrf(self, token: str | None) -> str:
        """Issue a fresh in-memory browser CSRF value for an existing cookie session."""
        self._require_enabled()
        csrf = secrets.token_urlsafe(32)
        con = self._connect()
        try:
            with immediate(con):
                owner = self._owner(con, token)
                con.execute(
                    "UPDATE sessions SET csrf_hash=? WHERE token_hash=?",
                    (_digest(csrf), owner),
                )
        finally:
            con.close()
        return csrf

    def _owner(
        self, con: sqlite3.Connection, token: str | None, csrf: str | None = None
    ) -> str:
        if not token or len(token) > 128:
            raise SandboxRejected("SESSION_REQUIRED", 401)
        owner = _digest(token)
        row = con.execute(
            "SELECT csrf_hash,expires_at FROM sessions WHERE token_hash=?", (owner,)
        ).fetchone()
        if row is None or row["expires_at"] <= time():
            raise SandboxRejected("SESSION_EXPIRED_OR_INVALID", 401)
        if csrf is not None and (
            len(csrf) > 128
            or not secrets.compare_digest(row["csrf_hash"], _digest(csrf))
        ):
            raise SandboxRejected("CSRF_INVALID", 403)
        return owner

    def scenarios(self) -> list[dict[str, str]]:
        self._require_enabled()
        return [{"scenario_id": sid, "label": f"Synthetic {sid}"} for sid in GUIDED]

    def capacity(self) -> dict:
        if not self.enabled:
            return {"sandbox": "DISABLED", "live": "DISABLED", "mode": "OFFLINE_ONLY"}
        self.sweep_expired()
        con = self._connect()
        try:
            day = datetime.now(timezone.utc).date().isoformat()
            row = con.execute(
                "SELECT reserved_turns FROM day_budget WHERE day=?", (day,)
            ).fetchone()
            used = row[0] if row else 0
            accepting = con.execute(
                "SELECT accept_new_runs FROM control WHERE id=1"
            ).fetchone()[0]
            return {
                "sandbox": "LOCAL_ENABLED",
                "live": "LOCAL_OLLAMA_ONLY" if self.local_live_enabled else "DISABLED",
                "mode": "LOCAL_OPTIONAL" if self.local_live_enabled else "OFFLINE_ONLY",
                "accepting_new_runs": bool(accepting),
                "offline_turn_slots_remaining": max(0, self.daily_turn_budget - used),
                "session_ttl_seconds": self.session_ttl,
            }
        finally:
            con.close()

    def start(
        self,
        token: str | None,
        csrf: str | None,
        *,
        scenario_id: str,
        mode: str,
        ticket_text: str | None,
        idempotency_key: str,
        client_key: str = "trusted-local",
        run_kind: str = "DETERMINISTIC_COMPONENT_TEST",
    ) -> tuple[str, bool]:
        self._require_enabled()
        self.sweep_expired()
        if not csrf:
            raise SandboxRejected("CSRF_INVALID", 403)
        if mode not in {"GUIDED", "CUSTOM"} or scenario_id not in GUIDED:
            raise SandboxRejected("UNSUPPORTED_SCENARIO")
        if run_kind not in {"DETERMINISTIC_COMPONENT_TEST", "LIVE"}:
            raise SandboxRejected("UNSUPPORTED_RUN_KIND")
        if run_kind == "LIVE" and not self.local_live_enabled:
            raise SandboxRejected("LIVE_UNAVAILABLE", 503)
        if run_kind == "LIVE" and (mode != "GUIDED" or scenario_id != "SC-01"):
            raise SandboxRejected("LIVE_SYNTHETIC_SC01_ONLY")
        if mode == "CUSTOM" and scenario_id != "SC-01":
            raise SandboxRejected("CUSTOM_FIXTURE_MUST_BE_SC01")
        if mode == "GUIDED" and ticket_text is not None:
            raise SandboxRejected("GUIDED_TEXT_NOT_ALLOWED")
        if mode == "CUSTOM":
            if (
                not ticket_text
                or not 12 <= len(ticket_text) <= 600
                or len(ticket_text.encode("utf-8")) > 1800
            ):
                raise SandboxRejected("TICKET_SIZE_INVALID")
            if any(ord(char) < 32 and char not in "\n\t" for char in ticket_text):
                raise SandboxRejected("TICKET_CONTROL_CHARACTER")
            if re.search(
                r"https?://|@[a-z0-9.-]+\.[a-z]{2,}|\b\+?\d[\d -]{8,}\d\b",
                ticket_text,
                re.I,
            ):
                raise SandboxRejected("REAL_DATA_OR_URL_NOT_ALLOWED")
        if not re.fullmatch(r"[A-Za-z0-9_-]{12,80}", idempotency_key):
            raise SandboxRejected("IDEMPOTENCY_KEY_INVALID")
        request_hash = _digest(
            json.dumps([run_kind, mode, scenario_id, ticket_text], ensure_ascii=False)
        )
        con = self._connect()
        try:
            with immediate(con):
                owner = self._owner(con, token, csrf)
                prior = con.execute(
                    "SELECT run_id,request_hash FROM runs WHERE token_hash=? AND idempotency_key=?",
                    (owner, idempotency_key),
                ).fetchone()
                if prior:
                    if prior["request_hash"] != request_hash:
                        raise SandboxRejected("IDEMPOTENCY_CONFLICT", 409)
                    return prior["run_id"], False
                if (
                    con.execute(
                        "SELECT accept_new_runs FROM control WHERE id=1"
                    ).fetchone()[0]
                    != 1
                ):
                    raise SandboxRejected("SANDBOX_KILL_SWITCH", 503)
                count = con.execute(
                    "SELECT count(*) FROM runs WHERE token_hash=? AND expires_at>?",
                    (owner, time()),
                ).fetchone()[0]
                if count >= MAX_RUNS_PER_SESSION:
                    raise SandboxRejected("SESSION_RUN_LIMIT", 429)
                if (
                    mode == "CUSTOM"
                    and con.execute(
                        "SELECT count(*) FROM runs WHERE token_hash=? AND mode='CUSTOM' AND expires_at>?",
                        (owner, time()),
                    ).fetchone()[0]
                    >= 2
                ):
                    raise SandboxRejected("CUSTOM_RUN_LIMIT", 429)
                if (
                    scenario_id != "SC-01"
                    and con.execute(
                        "SELECT count(*) FROM runs WHERE token_hash=? AND scenario_id!='SC-01' AND expires_at>?",
                        (owner, time()),
                    ).fetchone()[0]
                    >= 2
                ):
                    raise SandboxRejected("CHALLENGE_RUN_LIMIT", 429)
                active = con.execute(
                    "SELECT count(*) FROM runs WHERE status='RUNNING'"
                ).fetchone()[0]
                if active >= 2:
                    raise SandboxRejected("SANDBOX_BUSY", 429)
                day = datetime.now(timezone.utc).date().isoformat()
                used_row = con.execute(
                    "SELECT reserved_turns FROM day_budget WHERE day=?", (day,)
                ).fetchone()
                used = used_row[0] if used_row else 0
                if used + 4 > self.daily_turn_budget:
                    raise SandboxRejected("OFFLINE_CAPACITY_EXHAUSTED", 429)
                if not self.store.charge_client(
                    con,
                    client_key,
                    "run",
                    int(time() // 60),
                    3 if self.beta_restricted else 8,
                ):
                    raise SandboxRejected("CLIENT_RUN_RATE_LIMIT", 429)
                run_id = secrets.token_hex(16)
                created = time()
                con.execute(
                    "INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        run_id,
                        owner,
                        idempotency_key,
                        request_hash,
                        scenario_id,
                        mode,
                        "RUNNING",
                        None,
                        None,
                        None,
                        None,
                        f"run-{run_id}.db",
                        created,
                        min(
                            created + self.session_ttl,
                            con.execute(
                                "SELECT expires_at FROM sessions WHERE token_hash=?",
                                (owner,),
                            ).fetchone()[0],
                        ),
                    ),
                )
                if run_kind == "LIVE":
                    con.execute(
                        "INSERT INTO run_inference(run_id,run_kind) VALUES (?,'LIVE')",
                        (run_id,),
                    )
                con.execute(
                    "INSERT INTO day_budget(day,reserved_turns) VALUES (?,?) "
                    "ON CONFLICT(day) DO UPDATE SET reserved_turns=reserved_turns+4",
                    (day, used + 4),
                )
            self._execute(run_id, scenario_id, mode, ticket_text, run_kind)
            return run_id, True
        finally:
            con.close()

    def _run_path(self, name: str) -> Path:
        if not _RUN_NAME.fullmatch(name):
            raise RuntimeError("invalid internal run path")
        path = (self.root / "runs" / name).resolve()
        if path.parent != (self.root / "runs").resolve():
            raise RuntimeError("run path escaped sandbox root")
        return path

    def _execute(
        self,
        run_id: str,
        scenario_id: str,
        mode: str,
        ticket_text: str | None,
        run_kind: str,
    ) -> None:
        path = self._run_path(f"run-{run_id}.db")
        source_file = SOURCES
        temporary_source = self.root / "runs" / f"source-{run_id}.json"
        status, failure = "COMPLETE", None
        decision = b_status = execution_state = None
        transport = None
        try:
            if mode == "CUSTOM":
                source_doc = json.loads(SOURCES.read_text(encoding="utf-8"))
                source_doc["sources"][0]["body_text"] = ticket_text
                temporary_source.write_text(
                    json.dumps(source_doc, ensure_ascii=False), encoding="utf-8"
                )
                source_file = temporary_source
            initialize(path)
            seed_synthetic(
                path, source_file, None
            )  # No evaluator truth enters this database.
            con = open_existing(path)
            try:
                ticket = json.loads(
                    con.execute(
                        "SELECT manifest_json FROM scenarios WHERE scenario_id=?",
                        (scenario_id,),
                    ).fetchone()[0]
                )["ticket_id"]
                episode_id = f"sandbox-{run_id}"
                con.execute(
                    "INSERT INTO episodes VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        episode_id,
                        scenario_id,
                        EPOCH,
                        ticket,
                        1,
                        f"grant-{scenario_id[-2:]}",
                        run_kind,
                        "STARTED",
                        _stamp(),
                        None,
                    ),
                )
                con.execute(
                    "INSERT INTO episode_candidate_protocols VALUES (?,?,?)",
                    (episode_id, "V2_EXACT_QUOTE", _stamp()),
                )
            finally:
                con.close()
            transport = (
                ProviderAdapter(
                    provider="ollama-local",
                    model="gemma4:e4b",
                    base_url="http://127.0.0.1:11434/v1",
                    api_key="local",
                    authorized=True,
                    timeout=240,
                    max_requests=4,
                )
                if run_kind == "LIVE"
                else SandboxOfflineTransport(scenario_id, custom=mode == "CUSTOM")
            )
            result = ReadOnlyAgentRunner(
                path,
                transport,
                max_tokens=768,
                max_duration_seconds=900 if run_kind == "LIVE" else 20,
            ).run(
                episode_id,
                trusted_escalation_reason="STATUS_CONFLICT"
                if scenario_id == "SC-06"
                else None,
            )
            decision, b_status, execution_state = (
                result.decision,
                result.b_status,
                result.execution_state,
            )
        except AgentExecutionError as exc:
            if isinstance(exc.__cause__, OfflineTransportTimeout):
                status, failure = "TECHNICAL_ERROR", "PROVIDER_TIMEOUT_SIMULATED"
            elif isinstance(exc.__cause__, OfflineTransportRateLimit):
                status, failure = "TECHNICAL_ERROR", "PROVIDER_RATE_LIMIT_SIMULATED"
            elif isinstance(exc.__cause__, ProviderFailure):
                status, failure = "TECHNICAL_ERROR", "OFFLINE_TRANSPORT_FAILED"
            else:
                status, failure = "VALIDATION_FAILED", "AGENT_VALIDATION_FAILED"
        except Exception:
            status, failure = "TECHNICAL_ERROR", "SANDBOX_EXECUTION_FAILED"
        finally:
            if temporary_source.is_file():
                temporary_source.unlink()
            con = self._connect()
            try:
                con.execute(
                    "UPDATE runs SET status=?,failure_code=?,decision=?,b_status=?,execution_state=? "
                    "WHERE run_id=?",
                    (status, failure, decision, b_status, execution_state, run_id),
                )
                if run_kind == "LIVE":
                    con.execute(
                        "UPDATE run_inference SET model_requests=? WHERE run_id=?",
                        (getattr(transport, "request_count", 0), run_id),
                    )
            finally:
                con.close()

    def _owned_run(self, token: str | None, run_id: str) -> sqlite3.Row:
        self._require_enabled()
        self.sweep_expired()
        if not re.fullmatch(r"[0-9a-f]{32}", run_id):
            raise SandboxRejected("RUN_NOT_FOUND", 404)
        con = self._connect()
        try:
            owner = self._owner(con, token)
            row = con.execute(
                "SELECT runs.*, COALESCE(run_inference.run_kind, "
                "'DETERMINISTIC_COMPONENT_TEST') AS run_kind, "
                "COALESCE(run_inference.model_requests,0) AS model_requests "
                "FROM runs LEFT JOIN run_inference ON runs.run_id=run_inference.run_id "
                "WHERE runs.run_id=? AND runs.token_hash=? AND runs.expires_at>?",
                (run_id, owner, time()),
            ).fetchone()
            if row is None:
                raise SandboxRejected("RUN_NOT_FOUND", 404)
            return row
        finally:
            con.close()

    def status(self, token: str | None, run_id: str) -> dict:
        row = self._owned_run(token, run_id)
        uncertain_live = row["run_kind"] == "LIVE" and row["status"] in {
            "RUNNING",
            "INTERRUPTED",
            "TECHNICAL_ERROR",
        }
        return {
            "run_id": run_id,
            "status": row["status"],
            "failure_code": row["failure_code"],
            "mode": row["run_kind"],
            "model_invoked": None if uncertain_live else row["model_requests"] > 0,
            "model_requests": row["model_requests"],
            "request_count_note": (
                "UNKNOWN_AFTER_INTERRUPTION"
                if row["status"] in {"RUNNING", "INTERRUPTED"} and uncertain_live
                else "DISPATCHED_PROCESSING_UNKNOWN"
                if uncertain_live
                else "OBSERVED_BY_ADAPTER"
            ),
            "started_at": datetime.fromtimestamp(
                row["created_at"], timezone.utc
            ).isoformat(),
            "provider": "ollama-local" if row["run_kind"] == "LIVE" else None,
            "requested_model": "gemma4:e4b" if row["run_kind"] == "LIVE" else None,
            "protected_effect": "DISABLED",
            "scenario_id": row["scenario_id"],
            "decision": row["decision"],
            "b_status": row["b_status"],
            "execution_state": row["execution_state"],
        }

    def evidence(self, token: str | None, run_id: str) -> dict:
        row = self._owned_run(token, run_id)
        result = self.status(token, run_id)
        path = self._run_path(row["db_name"])
        if not path.is_file():
            return {**result, "timeline": [], "evidence_state": "UNAVAILABLE"}
        con = open_existing(path)
        con.row_factory = sqlite3.Row
        try:
            episode_id = f"sandbox-{run_id}"
            events = []
            invocations = con.execute(
                "SELECT invocation_id,agent_role,status,failure_code,turn_evidence_json,"
                "provider,model_id "
                "FROM agent_invocations WHERE episode_id=?",
                (episode_id,),
            ).fetchall()

            def append_invocation(role: str) -> None:
                for inv in invocations:
                    if inv["agent_role"] != role:
                        continue
                    turns = json.loads(inv["turn_evidence_json"])
                    events.append(
                        {
                            "stage": "AGENT_" + role,
                            "status": inv["status"],
                            "failure_code": inv["failure_code"],
                            "turns": len(turns),
                            "provider": inv["provider"],
                            "requested_model": inv["model_id"],
                            "observed_models": sorted(
                                {t["model_id"] for t in turns if t.get("model_id")}
                            ),
                            "token_usage": [
                                {
                                    k: t["usage"].get(k)
                                    for k in ("prompt_tokens", "completion_tokens")
                                }
                                for t in turns
                                if isinstance(t.get("usage"), dict)
                            ],
                            "requested_modes": [
                                t.get("requested_output_mode") for t in turns
                            ],
                            "validation": [
                                t.get("diagnostic", {}).get("stage")
                                for t in turns
                                if t.get("diagnostic")
                            ],
                        }
                    )
                    for tool in con.execute(
                        "SELECT tool_name,tool_status,observation_owner,observation_sent_turn,"
                        "continuation_turn FROM tool_events WHERE invocation_id=?",
                        (inv["invocation_id"],),
                    ):
                        events.append(
                            {
                                "stage": "SCOPED_TOOL",
                                "agent": role,
                                "tool": tool["tool_name"],
                                "status": tool["tool_status"],
                                "source_owner": tool["observation_owner"],
                                "observation_sent": tool["observation_sent_turn"] == 1,
                                "continued": tool["continuation_turn"] == 2,
                            }
                        )

            append_invocation("A")
            binding = con.execute(
                "SELECT binding_status,relevance_status,diagnostic_code,model_quotes_json,"
                "bound_spans_json FROM candidate_quote_bindings WHERE episode_id=?",
                (episode_id,),
            ).fetchone()
            if binding:
                quotes = json.loads(binding["model_quotes_json"])
                spans = json.loads(binding["bound_spans_json"])
                events.append(
                    {
                        "stage": "EXACT_SOURCE_BINDING",
                        "status": binding["binding_status"],
                        "relevance": binding["relevance_status"],
                        "diagnostic": binding["diagnostic_code"],
                        "scripted_quote"
                        if row["run_kind"] != "LIVE"
                        else "model_quote": quotes[0]["quotation"] if quotes else None,
                        "derived_span": spans[0] if spans else None,
                    }
                )
            candidate = con.execute(
                "SELECT candidate_id FROM candidate_requests WHERE episode_id=?",
                (episode_id,),
            ).fetchone()
            if candidate:
                events.append({"stage": "CANDIDATE_VALIDATED", "status": "PERSISTED"})
            handoff = con.execute(
                "SELECT handoff_id FROM handoffs WHERE episode_id=?", (episode_id,)
            ).fetchone()
            if handoff:
                events.append(
                    {
                        "stage": "H1_H4_HANDOFF",
                        "status": "VALID",
                        "compartments": [
                            "H1_TRUSTED_GRANT",
                            "H2_VERIFIED_FACTS",
                            "H3_UNTRUSTED_CANDIDATE",
                            "H4_SOURCE_REFERENCE",
                        ],
                    }
                )
            append_invocation("B")
            proposal = con.execute(
                "SELECT status,purpose,template_variant,selected_field_ids_json "
                "FROM agent_b_proposals WHERE episode_id=?",
                (episode_id,),
            ).fetchone()
            if proposal:
                events.append(
                    {
                        "stage": "BOUNDED_PROPOSAL",
                        "status": proposal["status"],
                        "purpose": proposal["purpose"],
                        "template_variant": proposal["template_variant"],
                        "selected_fields": json.loads(
                            proposal["selected_field_ids_json"]
                        )
                        if proposal["selected_field_ids_json"]
                        else [],
                    }
                )
            source = con.execute(
                "SELECT s.body_text,s.body_sha256,s.source_version FROM ticket_sources s "
                "JOIN episodes e ON e.ticket_id=s.ticket_id AND e.source_version=s.source_version "
                "WHERE e.episode_id=?",
                (episode_id,),
            ).fetchone()
            return {
                **result,
                "evidence_state": "AVAILABLE",
                "source": {
                    "text": source["body_text"],
                    "sha256": source["body_sha256"],
                    "version": source["source_version"],
                }
                if source
                else None,
                "timeline": events,
                "disclosure": (
                    "Owner-only synthetic source; local Ollama model requests; "
                    "no protected effect"
                    if row["run_kind"] == "LIVE"
                    else "Owner-only synthetic source; offline scripted tool turns; "
                    "zero provider generations"
                ),
            }
        finally:
            con.close()
