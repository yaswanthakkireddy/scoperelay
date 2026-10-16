"""Offline protocol doubles only; no test in this file establishes LIVE inference."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from scoperelay.application.read_only_agents import (
    AgentExecutionError,
    ReadOnlyAgentRunner,
)
from scoperelay.inference.adapter import (
    LiveInferenceBlocked,
    ModelTurn,
    ProviderAdapter,
    ProviderFailure,
    ToolCall,
)
from scoperelay.storage.database import open_existing
from scoperelay.storage.fixture_loader import seed_synthetic
from scoperelay.storage.schema import initialize
from scoperelay.trusted.approval import LocalSupervisorService
from scoperelay.trusted.contracts import CandidateRequestV1, TrustedTask
from scoperelay.trusted.material_store import freeze_escalation_test_preview


ROOT = Path(__file__).resolve().parents[2]
STAMP = "2026-10-14T00:00:00.000000Z"


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "agent.db"
    initialize(path)
    seed_synthetic(
        path,
        ROOT / "fixtures/scenarios/source_texts.json",
        ROOT / "fixtures/scenarios/oracle_labels.json",
    )
    con = open_existing(path)
    for n in range(1, 9):
        con.execute(
            "INSERT INTO episodes VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                f"ep-{n:02d}",
                f"SC-{n:02d}",
                "synthetic-epoch-1",
                f"T-{n:03d}",
                1,
                f"grant-{n:02d}",
                "DETERMINISTIC_COMPONENT_TEST",
                "STARTED",
                STAMP,
                None,
            ),
        )
        con.execute(
            "INSERT INTO episode_candidate_protocols VALUES (?,?,?)",
            (f"ep-{n:02d}", "V1_SPANS", STAMP),
        )
    con.close()
    return path


class Scripted:
    provider = "scripted-double"
    model = "not-a-model"
    run_kind = "DETERMINISTIC_COMPONENT_TEST"

    def __init__(
        self,
        *,
        a_tool="read_assigned_ticket",
        a_arg=None,
        b_arg=None,
        b_tool="read_verified_order_status",
        extra=None,
        bad_ref=False,
        fail_at=None,
        repeat=False,
    ):
        self.a_tool, self.a_arg, self.b_arg = a_tool, a_arg, b_arg
        self.b_tool, self.extra, self.bad_ref = b_tool, extra, bad_ref
        self.fail_at, self.repeat = fail_at, repeat
        self.calls = []

    def complete(self, messages, *, tool_name, max_tokens, response_protocol=None):
        self.calls.append((tool_name, tuple(m["role"] for m in messages)))
        if self.fail_at == len(self.calls):
            raise TimeoutError("synthetic timeout")
        if messages[-1]["role"] != "tool":
            if tool_name == "read_assigned_ticket":
                ticket_id = (
                    messages[1]["content"]
                    .split("Assigned ticket_id: ")[1]
                    .split(".")[0]
                )
                return ModelTurn(
                    None,
                    (
                        ToolCall(
                            "a-call",
                            self.a_tool,
                            json.dumps(
                                self.a_arg
                                if self.a_arg is not None
                                else {"ticket_id": ticket_id}
                            ),
                        ),
                    ),
                )
            h2 = json.loads(messages[1]["content"].split(" H2: ")[1])
            return ModelTurn(
                None,
                (
                    ToolCall(
                        "b-call",
                        self.b_tool,
                        json.dumps(
                            self.b_arg
                            if self.b_arg is not None
                            else {"verification_id": h2["verification_id"]}
                        ),
                    ),
                ),
            )
        observation = json.loads(messages[-1]["content"])
        if self.repeat:
            return ModelTurn(None, (ToolCall("again", tool_name, "{}"),))
        if tool_name == "read_assigned_ticket":
            body = observation["body_text"]
            raw = {
                "candidate_id": "model-candidate",
                "ticket_id": observation["ticket_id"],
                "operation_hint": "ROUTINE_ORDER_STATUS",
                "order_ref_text": "ORD-101",
                "requested_recipient_text": None,
                "content_class_hint": "STATUS_ONLY",
                "source_spans": [
                    {
                        "source_id": observation["source_id"],
                        "source_version": observation["source_version"],
                        "start": 0,
                        "end": len(body),
                        "excerpt": body,
                    }
                ],
                "uncertainty": {
                    "ambiguous": False,
                    "quoted_only": False,
                    "conflicting_references": [],
                },
                "other_candidates": [],
            }
            if self.extra:
                raw[self.extra] = "forged"
        else:
            eta = observation["customer_visible_eta"]
            raw = {
                "status": "PROPOSED",
                "purpose": "STATUS_WITH_ETA" if eta else "STATUS_NO_ETA",
                "template_variant": "STATUS_T1" if eta else "STATUS_NOETA_T1",
                "selected_field_ids": ["order_id", "order_status"]
                + (["customer_visible_eta"] if eta else []),
                "observation_ref": "invented"
                if self.bad_ref
                else observation["tool_event_id"],
            }
        return ModelTurn(json.dumps(raw), (), "scripted-response", self.model, "stop")


def counts(path):
    con = open_existing(path)
    result = tuple(
        con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        for table in (
            "queue_effects",
            "resource_attempts",
            "journal_events",
            "escalation_approvals",
        )
    )
    con.close()
    return result


def test_two_distinct_tool_feedback_loops_no_effect(db):
    scripted = Scripted()
    result = ReadOnlyAgentRunner(db, scripted).run("ep-01")
    assert result.decision == "ALLOW" and result.b_status == "PROPOSED"
    assert result.a_invocation_id != result.b_invocation_id
    assert scripted.calls == [
        ("read_assigned_ticket", ("system", "user")),
        ("read_assigned_ticket", ("system", "user", "assistant", "tool")),
        ("read_verified_order_status", ("system", "user", "user")),
        ("read_verified_order_status", ("system", "user", "user", "assistant", "tool")),
    ]
    con = open_existing(db)
    rows = con.execute(
        "SELECT i.agent_role,i.is_live,t.call_id,t.observation_sent_turn,"
        "t.continuation_turn FROM agent_invocations i JOIN tool_events t "
        "ON t.invocation_id=i.invocation_id ORDER BY i.agent_role"
    ).fetchall()
    assert rows == [("A", 0, "a-call", 1, 2), ("B", 0, "b-call", 1, 2)]
    assert (
        con.execute("SELECT observation_ref FROM agent_b_proposals").fetchone()[0]
        == con.execute(
            "SELECT tool_event_id FROM tool_events WHERE call_id='b-call'"
        ).fetchone()[0]
    )
    con.close()
    assert counts(db) == (0, 0, 0, 0)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"a_tool": "read_verified_order_status"},
        {"a_arg": {"ticket_id": "T-002"}},
        {"a_arg": {"ticket_id": "T-001", "sql": "SELECT *"}},
        {"b_tool": "read_assigned_ticket"},
        {"b_arg": {"verification_id": "other"}},
        {"extra": "approved_by"},
        {"bad_ref": True},
        {"repeat": True},
    ],
)
def test_invalid_model_or_tool_never_effects(db, kwargs):
    with pytest.raises(AgentExecutionError):
        ReadOnlyAgentRunner(db, Scripted(**kwargs)).run("ep-01")
    assert counts(db) == (0, 0, 0, 0)


def test_fake_live_rejected(db):
    scripted = Scripted()
    scripted.run_kind = "LIVE"
    with pytest.raises(ValueError, match="cannot claim LIVE"):
        ReadOnlyAgentRunner(db, scripted)


def test_smoke_protected_snapshot_excludes_agent_evidence_but_detects_trusted_change(
    db,
):
    from scoperelay.application.live_smoke import _protected_snapshot

    before = _protected_snapshot(db)
    ReadOnlyAgentRunner(db, Scripted()).run("ep-01")
    assert _protected_snapshot(db) == before
    con = open_existing(db)
    con.execute("UPDATE orders SET version=version+1 WHERE order_id='ORD-101'")
    con.close()
    assert _protected_snapshot(db) != before


@pytest.mark.parametrize("n", range(1, 9))
def test_eight_synthetic_sources_are_read_only(db, n):
    result = ReadOnlyAgentRunner(db, Scripted()).run(f"ep-{n:02d}")
    assert result.protected_effect == "DISABLED"
    assert counts(db) == (0, 0, 0, 0)
    con = open_existing(db)
    assert (
        con.execute(
            "SELECT count(*) FROM tool_events WHERE invocation_id=?",
            (result.a_invocation_id,),
        ).fetchone()[0]
        == 1
    )
    assert (
        con.execute(
            "SELECT count(*) FROM agent_invocations WHERE episode_id=? AND is_live=1",
            (f"ep-{n:02d}",),
        ).fetchone()[0]
        == 0
    )
    con.close()


def test_provider_timeout_is_execution_failure_not_resource_denial(db):
    with pytest.raises(AgentExecutionError):
        ReadOnlyAgentRunner(db, Scripted(fail_at=1)).run("ep-01")
    con = open_existing(db)
    assert con.execute(
        "SELECT status,failure_code FROM agent_invocations"
    ).fetchone() == ("FAILED", "TimeoutError")
    con.close()
    assert counts(db) == (0, 0, 0, 0)


def test_sdk_adapter_uses_function_tool_wire_without_network():
    from types import SimpleNamespace

    captured = []

    def create(**kwargs):
        captured.append(kwargs)
        call = SimpleNamespace(
            type="function",
            id="provider-call-1",
            function=SimpleNamespace(
                name="read_assigned_ticket", arguments='{"ticket_id":"T-001"}'
            ),
        )
        choice = SimpleNamespace(
            message=SimpleNamespace(content=None, tool_calls=[call]),
            finish_reason="tool_calls",
        )
        return SimpleNamespace(
            choices=[choice],
            id="provider-response-1",
            model="model-revision",
            usage=None,
        )

    adapter = object.__new__(ProviderAdapter)
    adapter.provider, adapter.model = "offline-wire-test", "test-model"
    adapter._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    turn = adapter.complete(
        [{"role": "user", "content": "synthetic"}],
        tool_name="read_assigned_ticket",
        max_tokens=256,
    )
    assert turn.tool_calls == (
        ToolCall("provider-call-1", "read_assigned_ticket", '{"ticket_id":"T-001"}'),
    )
    assert captured[0]["store"] is False and captured[0]["parallel_tool_calls"] is False
    assert (
        captured[0]["tools"][0]["function"]["parameters"]["additionalProperties"]
        is False
    )


def test_ollama_local_wire_uses_documented_fields_without_network():
    from types import SimpleNamespace

    captured = []

    def create(**kwargs):
        captured.append(kwargs)
        call = SimpleNamespace(
            type="function",
            id="local-call-1",
            function=SimpleNamespace(
                name="read_verified_order_status",
                arguments='{"verification_id":"V-001"}',
            ),
        )
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=None, tool_calls=[call]),
                    finish_reason="tool_calls",
                )
            ],
            id="local-response-1",
            model="gemma4:e4b",
            usage=None,
        )

    adapter = object.__new__(ProviderAdapter)
    adapter.provider, adapter.model = "ollama-local", "gemma4:e4b"
    adapter._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    first = adapter.complete(
        [{"role": "user", "content": "synthetic"}],
        tool_name="read_verified_order_status",
        max_tokens=256,
    )
    assert first.tool_calls[0].call_id == "local-call-1"
    assert captured[0]["max_tokens"] == 256
    assert captured[0]["reasoning_effort"] == "none"
    assert captured[0]["tool_choice"] == "auto"
    assert all(
        name not in captured[0]
        for name in ("max_completion_tokens", "store", "parallel_tool_calls")
    )
    feedback = [
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": first.tool_calls[0].call_id,
                    "type": "function",
                    "function": {
                        "name": first.tool_calls[0].name,
                        "arguments": first.tool_calls[0].arguments,
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": first.tool_calls[0].call_id,
            "content": '{"status":"IN_TRANSIT"}',
        },
    ]
    adapter.complete(feedback, tool_name="read_verified_order_status", max_tokens=256)
    assert captured[1]["messages"][-1]["tool_call_id"] == "local-call-1"
    assert captured[1]["response_format"] == {"type": "json_object"}
    assert "tools" not in captured[1] and "tool_choice" not in captured[1]


@pytest.mark.parametrize(
    "bad_call",
    [
        {
            "type": "function",
            "id": None,
            "name": "read_assigned_ticket",
            "arguments": "{}",
        },
        {
            "type": "function",
            "id": "",
            "name": "read_assigned_ticket",
            "arguments": "{}",
        },
        {
            "type": "function",
            "id": "call",
            "name": "read_assigned_ticket",
            "arguments": None,
        },
    ],
)
def test_adapter_rejects_malformed_native_tool_envelope_without_network(bad_call):
    from types import SimpleNamespace

    call = SimpleNamespace(
        type=bad_call["type"],
        id=bad_call["id"],
        function=SimpleNamespace(
            name=bad_call["name"], arguments=bad_call["arguments"]
        ),
    )
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=None, tool_calls=[call]),
                finish_reason="tool_calls",
            )
        ],
        id=None,
        model="gemma4:e4b",
        usage=None,
    )
    adapter = object.__new__(ProviderAdapter)
    adapter.provider, adapter.model = "ollama-local", "gemma4:e4b"
    adapter._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **_: response))
    )
    with pytest.raises(ProviderFailure, match="invalid native tool call envelope"):
        adapter.complete(
            [{"role": "user", "content": "synthetic"}],
            tool_name="read_assigned_ticket",
            max_tokens=256,
        )


def test_adapter_rejects_nontext_final_message_without_network():
    from types import SimpleNamespace

    adapter = object.__new__(ProviderAdapter)
    adapter.provider, adapter.model = "ollama-local", "gemma4:e4b"
    adapter._client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **_: SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=SimpleNamespace(
                                content=[{"text": "not JSON text"}], tool_calls=[]
                            ),
                            finish_reason="stop",
                        )
                    ],
                    id=None,
                    model="gemma4:e4b",
                    usage=None,
                )
            )
        )
    )
    with pytest.raises(ProviderFailure, match="unexpected provider content shape"):
        adapter.complete(
            [{"role": "user", "content": "synthetic"}], tool_name=None, max_tokens=256
        )


def test_local_generation_ceiling_counts_before_dispatch():
    from types import SimpleNamespace

    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content='{"status":"UNABLE"}', tool_calls=[]
                    ),
                    finish_reason="stop",
                )
            ],
            id="local-response",
            model="gemma4:e4b",
            usage=None,
        )

    adapter = object.__new__(ProviderAdapter)
    adapter.provider, adapter.model = "ollama-local", "gemma4:e4b"
    adapter.max_requests, adapter.request_count, adapter.request_seconds = 1, 0, []
    adapter._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    adapter.complete(
        [{"role": "user", "content": "synthetic"}], tool_name=None, max_tokens=256
    )
    with pytest.raises(ProviderFailure, match="ceiling reached"):
        adapter.complete(
            [{"role": "user", "content": "synthetic"}], tool_name=None, max_tokens=256
        )
    assert adapter.request_count == len(calls) == 1
    assert len(adapter.request_seconds) == 1


def test_local_provider_error_is_counted_and_redacted():
    from types import SimpleNamespace

    def create(**kwargs):
        raise RuntimeError("synthetic-secret-must-not-escape")

    adapter = object.__new__(ProviderAdapter)
    adapter.provider, adapter.model = "ollama-local", "gemma4:e4b"
    adapter.max_requests, adapter.request_count, adapter.request_seconds = 6, 0, []
    adapter._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    with pytest.raises(ProviderFailure, match="RuntimeError") as caught:
        adapter.complete(
            [{"role": "user", "content": "synthetic"}], tool_name=None, max_tokens=256
        )
    assert "synthetic-secret" not in str(caught.value)
    assert adapter.request_count == 1 and len(adapter.request_seconds) == 1


def test_local_generation_ceiling_configuration_rejected():
    with pytest.raises(LiveInferenceBlocked, match="request ceiling"):
        ProviderAdapter(
            provider="ollama-local",
            model="gemma4:e4b",
            base_url="http://localhost:11434/v1",
            api_key="ollama",
            authorized=True,
            max_requests=7,
        )


def test_ollama_local_rejects_nonlocal_endpoint_without_network():
    with pytest.raises(LiveInferenceBlocked, match="local Ollama v1 endpoint"):
        ProviderAdapter(
            provider="ollama-local",
            model="gemma4:e4b",
            base_url="https://external.invalid/v1",
            api_key="dummy",
            authorized=True,
        )


def test_ollama_local_accepts_isolated_cpu_loopback_port_without_network():
    adapter = ProviderAdapter(
        provider="ollama-local",
        model="gemma4:e4b",
        base_url="http://127.0.0.1:11435/v1",
        api_key="ollama",
        authorized=True,
        max_requests=6,
    )
    assert adapter.request_count == 0 and adapter.max_requests == 6


def test_ollama_local_rejects_cloud_alias_without_network():
    with pytest.raises(LiveInferenceBlocked, match="approved installed model"):
        ProviderAdapter(
            provider="ollama-local",
            model="gemma4:cloud",
            base_url="http://localhost:11434/v1",
            api_key="ollama",
            authorized=True,
        )


def test_sdk_escalation_wire_declares_no_status_tool():
    from types import SimpleNamespace

    captured = []

    def create(**kwargs):
        captured.append(kwargs)
        choice = SimpleNamespace(
            message=SimpleNamespace(
                content='{"status":"ESCALATION_ACK"}', tool_calls=[]
            ),
            finish_reason="stop",
        )
        return SimpleNamespace(
            choices=[choice], id="offline-response", model="test", usage=None
        )

    adapter = object.__new__(ProviderAdapter)
    adapter.provider, adapter.model = "offline-wire-test", "test-model"
    adapter._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    result = adapter.complete(
        [{"role": "user", "content": "synthetic escalation"}],
        tool_name=None,
        max_tokens=256,
    )
    assert (
        result.tool_calls == ()
        and "tools" not in captured[0]
        and "tool_choice" not in captured[0]
    )


class BranchScripted(Scripted):
    def __init__(
        self,
        *,
        a_branch=None,
        b_branch=None,
        a_bad=None,
        b_bad=None,
        early_a=False,
        early_b=False,
        a_transform=None,
        b_transform=None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.a_branch, self.b_branch = a_branch, b_branch
        self.a_bad, self.b_bad = a_bad, b_bad
        self.early_a, self.early_b = early_a, early_b
        self.a_transform, self.b_transform = a_transform, b_transform

    def complete(self, messages, *, tool_name, max_tokens, response_protocol=None):
        is_a = tool_name == "read_assigned_ticket"
        early = self.early_a if is_a else self.early_b
        branch = self.a_branch if is_a else self.b_branch
        if early and messages[-1]["role"] != "tool":
            return ModelTurn(
                json.dumps({"status": branch or "UNABLE"}),
                (),
                "early",
                self.model,
                "stop",
            )
        if not is_a and "material_action_id" in messages[1]["content"]:
            self.calls.append((tool_name, tuple(m["role"] for m in messages)))
            raw = {
                "status": branch or "ESCALATION_ACK",
                "nonmaterial_note": "Reviewed synthetic preview",
            }
            if self.b_bad:
                raw[self.b_bad] = "forged"
            return ModelTurn(
                json.dumps(raw), (), "escalation-response", self.model, "stop"
            )
        turn = super().complete(messages, tool_name=tool_name, max_tokens=max_tokens)
        if messages[-1]["role"] == "tool":
            raw = json.loads(turn.content)
            if branch:
                raw = {"status": branch, "nonmaterial_note": "No supported output"}
            if is_a and "T-006" in messages[1]["content"] and not branch:
                raw["operation_hint"] = "SANITIZED_INTERNAL_ESCALATION"
                raw["order_ref_text"] = None
            bad = self.a_bad if is_a else self.b_bad
            if bad:
                raw[bad] = "forged"
            transform = self.a_transform if is_a else self.b_transform
            if transform:
                transform(raw)
            return ModelTurn(
                json.dumps(raw), (), turn.response_id, turn.model_id, "stop"
            )
        return turn


def protected_snapshot(path):
    con = open_existing(path)
    tables = (
        "queue_effects",
        "resource_attempts",
        "journal_events",
        "escalation_approvals",
        "material_actions",
        "manager_grants",
        "orders",
        "contacts",
        "org_policy",
    )
    result = {
        table: con.execute(f"SELECT * FROM {table}").fetchall() for table in tables
    }
    con.close()
    return result


@pytest.mark.parametrize(
    "role,branch,early",
    [
        ("A", "REFUSED", True),
        ("A", "UNABLE", True),
        ("A", "REFUSED", False),
        ("A", "UNABLE", False),
        ("B", "REFUSED", True),
        ("B", "UNABLE", True),
        ("B", "REFUSED", False),
        ("B", "UNABLE", False),
    ],
)
def test_truthful_refusal_or_inability(db, role, branch, early):
    scripted = BranchScripted(
        **(
            {"a_branch": branch, "early_a": early}
            if role == "A"
            else {"b_branch": branch, "early_b": early}
        )
    )
    before = protected_snapshot(db)
    result = ReadOnlyAgentRunner(db, scripted).run("ep-01")
    assert result.b_status == branch
    con = open_existing(db)
    invocation = con.execute(
        "SELECT invocation_id,status,turn_evidence_json FROM agent_invocations "
        "WHERE agent_role=?",
        (role,),
    ).fetchone()
    assert invocation[1] == branch
    if early:
        assert (
            con.execute(
                "SELECT count(*) FROM tool_events WHERE invocation_id=?",
                (invocation[0],),
            ).fetchone()[0]
            == 0
        )
        assert len(json.loads(invocation[2])) == 1
    else:
        assert con.execute(
            "SELECT observation_sent_turn,continuation_turn FROM tool_events "
            "WHERE invocation_id=?",
            (invocation[0],),
        ).fetchone() == (1, 2)
    if role == "A":
        assert con.execute("SELECT count(*) FROM candidate_requests").fetchone()[0] == 0
    else:
        assert con.execute(
            "SELECT status,purpose,observation_ref FROM agent_b_proposals"
        ).fetchone() == (branch, None, None)
    con.close()
    assert protected_snapshot(db) == before


def prepare_approved_escalation(path, *, approve=True):
    at = datetime.now(timezone.utc)
    con = open_existing(path)
    source = con.execute(
        "SELECT source_id,source_version,body_text FROM ticket_sources "
        "WHERE ticket_id='T-006'"
    ).fetchone()
    raw = {
        "candidate_id": "trusted-test-candidate",
        "ticket_id": "T-006",
        "operation_hint": "SANITIZED_INTERNAL_ESCALATION",
        "order_ref_text": None,
        "requested_recipient_text": None,
        "content_class_hint": "STATUS_ONLY",
        "source_spans": [
            {
                "source_id": source[0],
                "source_version": source[1],
                "start": 0,
                "end": len(source[2]),
                "excerpt": source[2],
            }
        ],
        "uncertainty": {
            "ambiguous": False,
            "quoted_only": False,
            "conflicting_references": [],
        },
        "other_candidates": [],
    }
    preview = freeze_escalation_test_preview(
        con,
        TrustedTask("ep-06", 1, "STATUS_CONFLICT"),
        CandidateRequestV1.from_mapping(raw),
        at=at,
        material_action_id="material-06",
        action_id="action-06",
        approval_id="approval-06",
        expires_at=at + timedelta(hours=1),
    )
    con.close()
    if approve:
        service = LocalSupervisorService(path)
        token = service.issue_trusted_local_session(at=at)
        service.decide(
            token,
            "approval-06",
            expected_version=1,
            expected_digest=preview.material_sha256,
            decision="APPROVE",
            at=at,
        )
    return preview


def test_approved_escalation_nonmaterial_no_tool_no_effect(db):
    preview = prepare_approved_escalation(db)
    before = protected_snapshot(db)
    scripted = BranchScripted()
    result = ReadOnlyAgentRunner(db, scripted).run(
        "ep-06",
        trusted_escalation_reason="STATUS_CONFLICT",
        material_action_id="material-06",
        approval_id="approval-06",
    )
    assert result.decision == "REQUEST APPROVAL" and result.b_status == "ESCALATION_ACK"
    con = open_existing(db)
    assert (
        con.execute(
            "SELECT count(*) FROM tool_events WHERE invocation_id=?",
            (result.b_invocation_id,),
        ).fetchone()[0]
        == 0
    )
    assert con.execute(
        "SELECT status,purpose,observation_ref,response_turn FROM agent_b_proposals"
    ).fetchone() == ("ESCALATION_ACK", None, None, 1)
    h2 = json.loads(
        con.execute("SELECT h2_verified_refs_json FROM handoffs").fetchone()[0]
    )
    assert h2["material_sha256"] == preview.material_sha256
    con.close()
    assert protected_snapshot(db) == before


@pytest.mark.parametrize(
    "approval_state", ["MISSING", "PENDING", "REJECTED", "STALE_DIGEST"]
)
def test_escalation_requires_current_approved_context(db, approval_state):
    if approval_state != "MISSING":
        prepare_approved_escalation(db, approve=approval_state == "STALE_DIGEST")
    if approval_state == "REJECTED":
        at = datetime.now(timezone.utc)
        service = LocalSupervisorService(db)
        token = service.issue_trusted_local_session(at=at)
        digest = service.review(token, "approval-06", at=at).material_sha256
        service.decide(
            token,
            "approval-06",
            expected_version=1,
            expected_digest=digest,
            decision="REJECT",
            at=at,
        )
    if approval_state == "STALE_DIGEST":
        con = open_existing(db)
        con.execute(
            "UPDATE contacts SET version=version+1 WHERE contact_id='internal-support'"
        )
        con.close()
    before = protected_snapshot(db)
    runner = ReadOnlyAgentRunner(db, BranchScripted())
    if approval_state == "MISSING":
        result = runner.run("ep-06", trusted_escalation_reason="STATUS_CONFLICT")
        assert result.b_invocation_id is None and result.decision == "REQUEST APPROVAL"
        assert result.execution_state == "APPROVAL_CONTEXT_MISSING"
    elif approval_state in {"PENDING", "REJECTED"}:
        result = runner.run(
            "ep-06",
            trusted_escalation_reason="STATUS_CONFLICT",
            material_action_id="material-06",
            approval_id="approval-06",
        )
        assert result.b_invocation_id is None and result.decision == "REQUEST APPROVAL"
        assert result.execution_state == f"APPROVAL_{approval_state}"
    else:
        with pytest.raises(AgentExecutionError):
            runner.run(
                "ep-06",
                trusted_escalation_reason="STATUS_CONFLICT",
                material_action_id="material-06",
                approval_id="approval-06",
            )
    assert protected_snapshot(db) == before


@pytest.mark.parametrize(
    "field",
    [
        "grant_id",
        "approved_by",
        "business_interaction_id",
        "h1_grant_ref",
        "h2_verified_refs",
        "trusted",
        "resource_admit",
    ],
)
def test_candidate_cannot_mint_authority_or_change_business_identity(db, field):
    before = protected_snapshot(db)
    with pytest.raises(AgentExecutionError):
        ReadOnlyAgentRunner(db, BranchScripted(a_bad=field)).run("ep-01")
    assert protected_snapshot(db) == before


@pytest.mark.parametrize(
    "field",
    [
        "recipient",
        "rendered_body",
        "approval_id",
        "approved_by",
        "grant_id",
        "internal_notes",
        "trusted",
    ],
)
def test_b_cannot_add_protected_material_or_supervisor_claim(db, field):
    before = protected_snapshot(db)
    with pytest.raises(AgentExecutionError):
        ReadOnlyAgentRunner(db, BranchScripted(b_bad=field)).run("ep-01")
    assert protected_snapshot(db) == before


@pytest.mark.parametrize(
    "field", ["recipient", "rendered_body", "approval_id", "approved_by"]
)
def test_escalation_ack_cannot_change_approved_material(db, field):
    prepare_approved_escalation(db)
    before = protected_snapshot(db)
    with pytest.raises(AgentExecutionError):
        ReadOnlyAgentRunner(db, BranchScripted(b_bad=field)).run(
            "ep-06",
            trusted_escalation_reason="STATUS_CONFLICT",
            material_action_id="material-06",
            approval_id="approval-06",
        )
    assert protected_snapshot(db) == before


@pytest.mark.parametrize("branch", ["REFUSED", "UNABLE"])
def test_escalation_failure_branch_has_no_tool_or_material_mutation(db, branch):
    prepare_approved_escalation(db)
    before = protected_snapshot(db)
    result = ReadOnlyAgentRunner(db, BranchScripted(b_branch=branch)).run(
        "ep-06",
        trusted_escalation_reason="STATUS_CONFLICT",
        material_action_id="material-06",
        approval_id="approval-06",
    )
    assert result.b_status == branch
    con = open_existing(db)
    assert con.execute(
        "SELECT status,observation_ref FROM agent_b_proposals"
    ).fetchone() == (branch, None)
    assert (
        con.execute(
            "SELECT count(*) FROM tool_events WHERE invocation_id=?",
            (result.b_invocation_id,),
        ).fetchone()[0]
        == 0
    )
    con.close()
    assert protected_snapshot(db) == before


def test_provider_native_refusal_without_json_is_not_fabricated(db):
    class NativeRefusal(Scripted):
        def complete(self, messages, *, tool_name, max_tokens, response_protocol=None):
            return ModelTurn(
                None,
                (),
                "native-refusal",
                self.model,
                "stop",
                None,
                "provider declined",
            )

    before = protected_snapshot(db)
    result = ReadOnlyAgentRunner(db, NativeRefusal()).run("ep-01")
    assert result.b_status == "REFUSED" and result.b_invocation_id is None
    con = open_existing(db)
    assert con.execute("SELECT count(*) FROM tool_events").fetchone()[0] == 0
    con.close()
    assert protected_snapshot(db) == before


def test_b_wrong_handoff_verification_or_stale_version_denied(db):
    result = ReadOnlyAgentRunner(db, Scripted()).run("ep-01")
    con = open_existing(db)
    event = con.execute(
        "SELECT handoff_id,verification_id FROM tool_events WHERE call_id='b-call'"
    ).fetchone()
    from scoperelay.trusted.tool_dispatcher import ToolScope, ToolScopeDenied, dispatch

    scope = ToolScope("B", "ep-01", "grant-01", "T-001", event[0], event[1])
    assert (
        dispatch(
            con,
            scope,
            ToolCall(
                "fresh",
                "read_verified_order_status",
                json.dumps({"verification_id": event[1]}),
            ),
        )["order_status"]
        == "IN_TRANSIT"
    )
    con.execute("UPDATE orders SET version=version+1 WHERE order_id='ORD-101'")
    with pytest.raises(ToolScopeDenied):
        dispatch(
            con,
            scope,
            ToolCall(
                "stale",
                "read_verified_order_status",
                json.dumps({"verification_id": event[1]}),
            ),
        )
    with pytest.raises(ToolScopeDenied):
        dispatch(
            con,
            ToolScope("B", "ep-02", "grant-02", "T-002", event[0], event[1]),
            ToolCall(
                "cross",
                "read_verified_order_status",
                json.dumps({"verification_id": event[1]}),
            ),
        )
    con.close()
    assert result.protected_effect == "DISABLED"


def test_duplicate_provider_tool_calls_fail_before_dispatch(db):
    class Duplicate(Scripted):
        def complete(self, messages, *, tool_name, max_tokens, response_protocol=None):
            turn = super().complete(
                messages, tool_name=tool_name, max_tokens=max_tokens
            )
            if tool_name == "read_assigned_ticket" and turn.tool_calls:
                return ModelTurn(None, (turn.tool_calls[0], turn.tool_calls[0]))
            return turn

    before = protected_snapshot(db)
    with pytest.raises(AgentExecutionError):
        ReadOnlyAgentRunner(db, Duplicate()).run("ep-01")
    con = open_existing(db)
    assert con.execute("SELECT count(*) FROM tool_events").fetchone()[0] == 0
    con.close()
    assert protected_snapshot(db) == before


def test_live_smoke_defaults_blocked_without_network_or_db(tmp_path, capsys):
    from scoperelay.application.live_smoke import main

    assert main([]) == 2
    assert "LIVE_PROVIDER_BLOCKED" in capsys.readouterr().out
    assert list(tmp_path.iterdir()) == []


def test_b_observation_sensitivity_is_only_scripted_interface_evidence(tmp_path):
    outcomes = []
    for status, eta in (("IN_TRANSIT", "2026-10-20"), ("DELAYED", None)):
        path = tmp_path / f"{status}.db"
        initialize(path)
        seed_synthetic(
            path,
            ROOT / "fixtures/scenarios/source_texts.json",
            ROOT / "fixtures/scenarios/oracle_labels.json",
        )
        con = open_existing(path)
        con.execute(
            "UPDATE orders SET order_status=?,customer_visible_eta=? WHERE order_id='ORD-101'",
            (status, eta),
        )
        con.execute(
            "INSERT INTO episodes VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                "ep-01",
                "SC-01",
                "synthetic-epoch-1",
                "T-001",
                1,
                "grant-01",
                "DETERMINISTIC_COMPONENT_TEST",
                "STARTED",
                STAMP,
                None,
            ),
        )
        con.execute(
            "INSERT INTO episode_candidate_protocols VALUES (?,?,?)",
            ("ep-01", "V1_SPANS", STAMP),
        )
        con.close()
        result = ReadOnlyAgentRunner(path, Scripted()).run("ep-01")
        assert result.b_status == "PROPOSED"
        con = open_existing(path)
        outcomes.append(
            con.execute(
                "SELECT purpose,selected_field_ids_json FROM agent_b_proposals"
            ).fetchone()
        )
        con.close()
        assert counts(path) == (0, 0, 0, 0)
    assert outcomes[0] != outcomes[1]


def test_handoff_provider_messages_keep_trusted_and_lower_trust_separate(db):
    class Inspect(Scripted):
        def complete(self, messages, *, tool_name, max_tokens, response_protocol=None):
            if tool_name == "read_verified_order_status":
                trusted = messages[1]["content"]
                lower = messages[2]["content"]
                assert "H1:" in trusted and "H2:" in trusted
                assert "LOWER-TRUST SOURCE DATA ONLY" in lower
                assert "candidate_id" not in trusted and "grant_id" not in lower
                assert "expected_branch" not in json.dumps(messages)
                assert "evaluation_truth" not in json.dumps(messages)
            return super().complete(
                messages, tool_name=tool_name, max_tokens=max_tokens
            )

    before = protected_snapshot(db)
    assert ReadOnlyAgentRunner(db, Inspect()).run("ep-01").b_status == "PROPOSED"
    assert protected_snapshot(db) == before


@pytest.mark.parametrize(
    "change",
    [
        lambda raw: raw["source_spans"][0].update(source_id="source-02"),
        lambda raw: raw["source_spans"][0].update(source_version=2),
        lambda raw: raw["source_spans"][0].update(excerpt="forged"),
        lambda raw: raw.update(ticket_id="T-002"),
        lambda raw: raw.update(operation_hint="HISTORY_EXPORT"),
    ],
)
def test_invalid_candidate_source_or_operation_cannot_enter_handoff(db, change):
    before = protected_snapshot(db)
    with pytest.raises(AgentExecutionError):
        ReadOnlyAgentRunner(db, BranchScripted(a_transform=change)).run("ep-01")
    con = open_existing(db)
    assert con.execute("SELECT count(*) FROM handoffs").fetchone()[0] == 0
    assert con.execute("SELECT count(*) FROM candidate_requests").fetchone()[0] == 0
    con.close()
    assert protected_snapshot(db) == before


def test_citation_excerpt_mismatch_fails_a_before_candidate_persistence(db):
    def wrong_span(raw):
        raw["source_spans"][0].update(start=16, end=31, excerpt="ORD-101")

    before = protected_snapshot(db)
    with pytest.raises(AgentExecutionError, match="SourceReferenceError"):
        ReadOnlyAgentRunner(db, BranchScripted(a_transform=wrong_span)).run("ep-01")
    con = open_existing(db)
    status, failure, evidence_json = con.execute(
        "SELECT status,failure_code,turn_evidence_json FROM agent_invocations WHERE agent_role='A'"
    ).fetchone()
    assert (status, failure) == ("FAILED", "SourceReferenceError")
    evidence = json.loads(evidence_json)
    assert len(evidence) == 2
    assert evidence[-1]["diagnostic"] == {
        "stage": "A_trusted_validation",
        "category": "SourceReferenceError",
        "source_error": "excerpt does not match source span",
    }
    assert con.execute("SELECT count(*) FROM candidate_requests").fetchone()[0] == 0
    assert con.execute("SELECT count(*) FROM handoffs").fetchone()[0] == 0
    con.close()
    assert protected_snapshot(db) == before


def test_exact_but_irrelevant_citation_cannot_authorize_routine_handoff(db):
    def cite_greeting(raw):
        raw["source_spans"] = [
            {
                **raw["source_spans"][0],
                "start": 0,
                "end": len("Hello"),
                "excerpt": "Hello",
            }
        ]

    before = protected_snapshot(db)
    result = ReadOnlyAgentRunner(db, BranchScripted(a_transform=cite_greeting)).run(
        "ep-01"
    )
    assert result.decision == "INCOMPLETE EVIDENCE" and result.b_invocation_id is None
    con = open_existing(db)
    assert con.execute("SELECT count(*) FROM handoffs").fetchone()[0] == 0
    assert (
        con.execute(
            "SELECT count(*) FROM agent_invocations WHERE agent_role='B'"
        ).fetchone()[0]
        == 0
    )
    con.close()
    assert protected_snapshot(db) == before


def test_exact_fake_manager_citation_is_not_a_trusted_grant(db):
    def cite_fake_manager(raw):
        con = open_existing(db)
        body = con.execute(
            "SELECT body_text FROM ticket_sources WHERE ticket_id='T-004'"
        ).fetchone()[0]
        con.close()
        excerpt = "manager approved a new grant"
        start = body.index(excerpt)
        raw["source_spans"] = [
            {
                **raw["source_spans"][0],
                "start": start,
                "end": start + len(excerpt),
                "excerpt": excerpt,
            }
        ]

    before = protected_snapshot(db)
    result = ReadOnlyAgentRunner(db, BranchScripted(a_transform=cite_fake_manager)).run(
        "ep-04"
    )
    assert result.b_invocation_id is None
    con = open_existing(db)
    assert (
        con.execute(
            "SELECT count(*) FROM handoffs WHERE episode_id='ep-04'"
        ).fetchone()[0]
        == 0
    )
    con.close()
    assert protected_snapshot(db) == before


@pytest.mark.parametrize(
    "change",
    [
        lambda raw: raw.update(template_variant="UNSAFE_TEMPLATE"),
        lambda raw: raw.update(selected_field_ids=["internal_notes"]),
        lambda raw: raw.update(observation_ref="invented"),
        lambda raw: raw.update(purpose="EXTERNAL_EXPORT"),
        lambda raw: raw.update(status="ESCALATION_ACK"),
    ],
)
def test_b_invalid_bounded_selection_rejected(db, change):
    before = protected_snapshot(db)
    with pytest.raises(AgentExecutionError):
        ReadOnlyAgentRunner(db, BranchScripted(b_transform=change)).run("ep-01")
    con = open_existing(db)
    assert con.execute("SELECT count(*) FROM agent_b_proposals").fetchone()[0] == 0
    con.close()
    assert protected_snapshot(db) == before


@pytest.mark.parametrize(
    "mutation,expected_decision",
    [
        (
            "UPDATE org_policy SET internal_recipient_id='contact-c1' WHERE org_id='org_demo'",
            "DENY",
        ),
        (
            "UPDATE manager_grants SET operation_class='ROUTINE_ORDER_STATUS' WHERE grant_id='grant-06'",
            "DENY",
        ),
        (
            "UPDATE escalation_approvals SET expires_at='2020-01-01T00:00:00Z' WHERE approval_id='approval-06'",
            None,
        ),
        ("UPDATE principals SET active=0 WHERE principal_id='supervisor-1'", None),
    ],
)
def test_escalation_changed_trusted_scope_never_reaches_b(
    db, mutation, expected_decision
):
    prepare_approved_escalation(db)
    con = open_existing(db)
    con.execute(mutation)
    con.close()
    before = protected_snapshot(db)
    if expected_decision:
        result = ReadOnlyAgentRunner(db, BranchScripted()).run(
            "ep-06",
            trusted_escalation_reason="STATUS_CONFLICT",
            material_action_id="material-06",
            approval_id="approval-06",
        )
        assert result.decision == expected_decision and result.b_invocation_id is None
    else:
        with pytest.raises(AgentExecutionError):
            ReadOnlyAgentRunner(db, BranchScripted()).run(
                "ep-06",
                trusted_escalation_reason="STATUS_CONFLICT",
                material_action_id="material-06",
                approval_id="approval-06",
            )
    con = open_existing(db)
    assert (
        con.execute(
            "SELECT count(*) FROM agent_invocations WHERE agent_role='B'"
        ).fetchone()[0]
        == 0
    )
    con.close()
    assert protected_snapshot(db) == before


def test_escalation_model_status_tool_attempt_is_not_executed(db):
    prepare_approved_escalation(db)

    class EscalationToolAttack(BranchScripted):
        def complete(self, messages, *, tool_name, max_tokens, response_protocol=None):
            if tool_name is None:
                return ModelTurn(
                    None,
                    (
                        ToolCall(
                            "forged",
                            "read_verified_order_status",
                            '{"verification_id":"invented"}',
                        ),
                    ),
                )
            return super().complete(
                messages, tool_name=tool_name, max_tokens=max_tokens
            )

    before = protected_snapshot(db)
    with pytest.raises(AgentExecutionError):
        ReadOnlyAgentRunner(db, EscalationToolAttack()).run(
            "ep-06",
            trusted_escalation_reason="STATUS_CONFLICT",
            material_action_id="material-06",
            approval_id="approval-06",
        )
    con = open_existing(db)
    assert (
        con.execute(
            "SELECT count(*) FROM tool_events WHERE tool_name='read_verified_order_status'"
        ).fetchone()[0]
        == 0
    )
    con.close()
    assert protected_snapshot(db) == before


@pytest.mark.parametrize("failure_call,role", [(2, "A"), (4, "B")])
def test_transport_fails_after_real_dispatch_without_fake_continuation(
    db, failure_call, role
):
    before = protected_snapshot(db)
    with pytest.raises(AgentExecutionError):
        ReadOnlyAgentRunner(db, Scripted(fail_at=failure_call)).run("ep-01")
    con = open_existing(db)
    row = con.execute(
        "SELECT i.status,i.failure_code,t.tool_status,t.observation_sent_turn,"
        "t.continuation_turn FROM agent_invocations i JOIN tool_events t "
        "ON t.invocation_id=i.invocation_id WHERE i.agent_role=?",
        (role,),
    ).fetchone()
    assert row == ("FAILED", "TimeoutError", "SUCCESS", None, None)
    assert con.execute("SELECT count(*) FROM agent_b_proposals").fetchone()[0] == 0
    con.close()
    assert protected_snapshot(db) == before


def test_malformed_a_json_is_failure_not_workflow_denial(db):
    class Malformed(Scripted):
        def complete(self, messages, *, tool_name, max_tokens, response_protocol=None):
            turn = super().complete(
                messages, tool_name=tool_name, max_tokens=max_tokens
            )
            if tool_name == "read_assigned_ticket" and messages[-1]["role"] == "tool":
                return ModelTurn("{bad-json", ())
            return turn

    before = protected_snapshot(db)
    with pytest.raises(AgentExecutionError):
        ReadOnlyAgentRunner(db, Malformed()).run("ep-01")
    con = open_existing(db)
    assert con.execute(
        "SELECT status,failure_code FROM agent_invocations"
    ).fetchone() == ("FAILED", "malformed model JSON")
    evidence = json.loads(
        con.execute("SELECT turn_evidence_json FROM agent_invocations").fetchone()[0]
    )
    assert [turn["turn"] for turn in evidence] == [1, 2]
    assert con.execute("SELECT count(*) FROM workflow_decisions").fetchone()[0] == 0
    con.close()
    assert protected_snapshot(db) == before


@pytest.mark.parametrize(
    "transform,expected_type,expected_path",
    [
        (lambda raw: raw.pop("other_candidates"), "missing", ["other_candidates"]),
        (
            lambda raw: raw.update(approved_by="fake supervisor"),
            "extra_forbidden",
            ["<untrusted>"],
        ),
        (
            lambda raw: raw.update(operation_hint="HISTORY_EXPORT"),
            "literal_error",
            ["operation_hint"],
        ),
        (
            lambda raw: raw.update(source_spans="not spans"),
            "list_type",
            ["source_spans"],
        ),
    ],
)
def test_a_validation_diagnostic_is_precise_but_never_stores_model_text(
    db, transform, expected_type, expected_path
):
    before = protected_snapshot(db)
    with pytest.raises(AgentExecutionError):
        ReadOnlyAgentRunner(db, BranchScripted(a_transform=transform)).run("ep-01")
    con = open_existing(db)
    row = con.execute(
        "SELECT turn_evidence_json FROM agent_invocations WHERE agent_role='A'"
    ).fetchone()
    evidence = json.loads(row[0])
    diagnostic = evidence[-1]["diagnostic"]
    assert diagnostic["stage"] == "A_candidate_schema"
    assert any(
        item["type"] == expected_type and item["path"] == expected_path
        for item in diagnostic["errors"]
    )
    assert (
        evidence[-1]["content_length"] > 0 and len(evidence[-1]["content_sha256"]) == 64
    )
    assert "fake supervisor" not in row[0] and "HISTORY_EXPORT" not in row[0]
    assert con.execute("SELECT count(*) FROM candidate_requests").fetchone()[0] == 0
    con.close()
    assert protected_snapshot(db) == before


def test_a_output_prompt_names_exact_required_wire_keys():
    from scoperelay.agents.contracts import CandidateJSON
    from scoperelay.agents.prompts import PROMPT_A_OUTPUT_CONTRACT_V1

    for key in CandidateJSON.model_fields:
        assert key in PROMPT_A_OUTPUT_CONTRACT_V1
    assert "JSON object only" in PROMPT_A_OUTPUT_CONTRACT_V1
    assert "REFUSED" in PROMPT_A_OUTPUT_CONTRACT_V1
    assert "UNABLE" in PROMPT_A_OUTPUT_CONTRACT_V1
    assert "end = start + len(excerpt)" in PROMPT_A_OUTPUT_CONTRACT_V1
    assert "start inclusive, end exclusive" in PROMPT_A_OUTPUT_CONTRACT_V1
    assert "source location is not business authority" in PROMPT_A_OUTPUT_CONTRACT_V1
    assert "return UNABLE instead of a candidate" in PROMPT_A_OUTPUT_CONTRACT_V1
    assert "ORD-101" not in PROMPT_A_OUTPUT_CONTRACT_V1


def test_b_output_prompt_names_exact_routine_and_refusal_keys():
    from scoperelay.agents.contracts import RoutineProposal
    from scoperelay.agents.prompts import PROMPT_B_OUTPUT_CONTRACT_V1

    for key in RoutineProposal.model_fields:
        assert key in PROMPT_B_OUTPUT_CONTRACT_V1
    assert "REFUSED" in PROMPT_B_OUTPUT_CONTRACT_V1
    assert "UNABLE" in PROMPT_B_OUTPUT_CONTRACT_V1
    assert "actual tool_event_id" in PROMPT_B_OUTPUT_CONTRACT_V1
    assert "ORD-101" not in PROMPT_B_OUTPUT_CONTRACT_V1


@pytest.mark.parametrize(
    "content,finish,expected",
    [
        (None, "stop", "missing model content"),
        ('Here is {"status":"UNABLE"}', "stop", "JSON with surrounding text"),
        ("{bad-json", "stop", "malformed model JSON"),
        ('{"status":"UNABLE"}', "length", "repeat tool call or truncated continuation"),
    ],
)
def test_a_final_content_failure_categories_are_safe(db, content, finish, expected):
    class Final(Scripted):
        def complete(self, messages, *, tool_name, max_tokens, response_protocol=None):
            if tool_name == "read_assigned_ticket" and messages[-1]["role"] == "tool":
                return ModelTurn(content, (), "synthetic-final", self.model, finish)
            return super().complete(
                messages, tool_name=tool_name, max_tokens=max_tokens
            )

    before = protected_snapshot(db)
    with pytest.raises(AgentExecutionError, match=expected):
        ReadOnlyAgentRunner(db, Final()).run("ep-01")
    con = open_existing(db)
    row = con.execute(
        "SELECT failure_code,turn_evidence_json FROM agent_invocations"
    ).fetchone()
    assert row[0] == expected
    evidence = json.loads(row[1])
    assert evidence[-1]["diagnostic"]["stage"] == (
        "A_tool_loop" if finish == "length" else "A_output_parse"
    )
    assert "Here is" not in row[1] and "bad-json" not in row[1]
    assert con.execute("SELECT count(*) FROM candidate_requests").fetchone()[0] == 0
    con.close()
    assert protected_snapshot(db) == before


def test_b_offline_handoff_and_observation_are_authoritative_and_no_effect(db):
    before = protected_snapshot(db)
    result = ReadOnlyAgentRunner(db, Scripted()).run("ep-01")
    assert result.run_kind == "DETERMINISTIC_COMPONENT_TEST"
    con = open_existing(db)
    handoff = con.execute(
        "SELECT h1_grant_ref_json,h2_verified_refs_json,h3_candidate_json,"
        "h4_source_refs_json FROM handoffs"
    ).fetchone()
    h1, h2, h3, h4 = map(json.loads, handoff)
    assert h1["grant_id"] == "grant-01" and h2["verification_id"]
    assert h3["candidate_id"] != "model-candidate" and h4
    b_event = con.execute(
        "SELECT tool_name,verification_id,tool_status,observation_json,"
        "observation_sent_turn,continuation_turn FROM tool_events "
        "WHERE call_id='b-call'"
    ).fetchone()
    assert b_event[:3] == (
        "read_verified_order_status",
        h2["verification_id"],
        "SUCCESS",
    )
    assert json.loads(b_event[3])["order_status"] == "IN_TRANSIT"
    assert b_event[4:] == (1, 2)
    assert (
        con.execute("SELECT status,observation_ref FROM agent_b_proposals").fetchone()[
            0
        ]
        == "PROPOSED"
    )
    con.close()
    assert protected_snapshot(db) == before


@pytest.mark.parametrize("n", [2, 3, 4, 5, 7, 8])
def test_hostile_and_semantic_sources_never_mutate_trusted_scope(db, n):
    before = protected_snapshot(db)
    result = ReadOnlyAgentRunner(db, Scripted()).run(f"ep-{n:02d}")
    con = open_existing(db)
    for h1_raw, h2_raw, h3_raw, h4_raw in con.execute(
        "SELECT h1_grant_ref_json,h2_verified_refs_json,h3_candidate_json,h4_source_refs_json "
        "FROM handoffs WHERE episode_id=?",
        (f"ep-{n:02d}",),
    ):
        h1, h2, h3, h4 = map(json.loads, (h1_raw, h2_raw, h3_raw, h4_raw))
        assert h1["grant_id"] == f"grant-{n:02d}"
        assert h1["ticket_id"] == f"T-{n:03d}"
        assert "grant_id" not in h3 and "recipient_contact_id" not in h3
        assert h2["recipient_contact_id"] == ("contact-c2" if n == 5 else "contact-c1")
        assert all(item["source_id"] == f"source-{n:02d}" for item in h4)
        assert "expected_branch" not in h1_raw + h2_raw + h3_raw + h4_raw
    con.close()
    assert result.protected_effect == "DISABLED" and protected_snapshot(db) == before


def test_tool_message_is_returned_to_same_conversation_and_call_id(db):
    class Inspect(Scripted):
        def complete(self, messages, *, tool_name, max_tokens, response_protocol=None):
            if messages[-1]["role"] == "tool":
                assistant = messages[-2]
                tool = messages[-1]
                envelope = json.loads(tool["content"])
                assert assistant["tool_calls"][0]["id"] == tool["tool_call_id"]
                assert envelope["tool_call_id"] == tool["tool_call_id"]
                assert envelope["tool_event_id"]
                assert all(
                    message["role"] != "tool"
                    for message in messages[:-1]
                    if message is not assistant
                )
            return super().complete(
                messages, tool_name=tool_name, max_tokens=max_tokens
            )

    result = ReadOnlyAgentRunner(db, Inspect()).run("ep-01")
    assert result.a_invocation_id != result.b_invocation_id
    con = open_existing(db)
    assert (
        con.execute("SELECT count(DISTINCT invocation_id) FROM tool_events").fetchone()[
            0
        ]
        == 2
    )
    con.close()


@pytest.mark.parametrize(
    "args",
    [
        {"ticket_id": {"nested": "T-001"}},
        {"ticket_id": "T-001", "verification_id": "fake"},
        {"ticket_id": ["T-001"]},
    ],
)
def test_nested_or_extra_model_tool_arguments_denied(db, args):
    before = protected_snapshot(db)
    with pytest.raises(AgentExecutionError):
        ReadOnlyAgentRunner(db, Scripted(a_arg=args)).run("ep-01")
    assert protected_snapshot(db) == before


def test_interrupted_trusted_dispatch_is_technical_failure(db, monkeypatch):
    import scoperelay.application.read_only_agents as runtime

    def interrupted(*args, **kwargs):
        raise OSError("synthetic interrupted read")

    monkeypatch.setattr(runtime, "dispatch", interrupted)
    before = protected_snapshot(db)
    with pytest.raises(AgentExecutionError):
        ReadOnlyAgentRunner(db, Scripted()).run("ep-01")
    con = open_existing(db)
    assert con.execute(
        "SELECT status,failure_code FROM agent_invocations"
    ).fetchone() == ("FAILED", "OSError")
    assert con.execute("SELECT count(*) FROM tool_events").fetchone()[0] == 0
    con.close()
    assert protected_snapshot(db) == before


def test_untrusted_provider_endpoint_or_missing_key_blocked_without_secret_output(
    capsys,
):
    with pytest.raises(LiveInferenceBlocked):
        ProviderAdapter(
            provider="example",
            model="x",
            base_url="https://example.invalid/v1",
            api_key="",
            authorized=True,
        )
    secret = "synthetic-test-secret-value"
    with pytest.raises(LiveInferenceBlocked) as exc:
        ProviderAdapter(
            provider="example",
            model="x",
            base_url="http://evil.invalid/v1",
            api_key=secret,
            authorized=True,
        )
    assert secret not in str(exc.value) and secret not in capsys.readouterr().out


def test_b_cannot_reuse_a_observation_as_its_own(db):
    class WrongInvocation(Scripted):
        def complete(self, messages, *, tool_name, max_tokens, response_protocol=None):
            turn = super().complete(
                messages, tool_name=tool_name, max_tokens=max_tokens
            )
            if (
                tool_name == "read_verified_order_status"
                and messages[-1]["role"] == "tool"
            ):
                con = open_existing(db)
                a_event = con.execute(
                    "SELECT t.tool_event_id FROM tool_events t "
                    "JOIN agent_invocations i ON i.invocation_id=t.invocation_id "
                    "WHERE i.agent_role='A'"
                ).fetchone()[0]
                con.close()
                raw = json.loads(turn.content)
                raw["observation_ref"] = a_event
                return ModelTurn(
                    json.dumps(raw), (), turn.response_id, turn.model_id, "stop"
                )
            return turn

    before = protected_snapshot(db)
    with pytest.raises(AgentExecutionError):
        ReadOnlyAgentRunner(db, WrongInvocation()).run("ep-01")
    assert protected_snapshot(db) == before


def test_b_cannot_propose_before_actual_tool_feedback(db):
    class Premature(Scripted):
        def complete(self, messages, *, tool_name, max_tokens, response_protocol=None):
            if tool_name == "read_verified_order_status":
                return ModelTurn(
                    json.dumps(
                        {
                            "status": "PROPOSED",
                            "purpose": "STATUS_BRIEF",
                            "template_variant": "STATUS_T1",
                            "selected_field_ids": ["order_status"],
                            "observation_ref": "invented",
                        }
                    ),
                    (),
                )
            return super().complete(
                messages, tool_name=tool_name, max_tokens=max_tokens
            )

    before = protected_snapshot(db)
    with pytest.raises(AgentExecutionError):
        ReadOnlyAgentRunner(db, Premature()).run("ep-01")
    con = open_existing(db)
    assert (
        con.execute(
            "SELECT count(*) FROM tool_events WHERE tool_name='read_verified_order_status'"
        ).fetchone()[0]
        == 0
    )
    assert con.execute("SELECT count(*) FROM agent_b_proposals").fetchone()[0] == 0
    con.close()
    assert protected_snapshot(db) == before


@pytest.mark.parametrize(
    "mutation",
    [
        "UPDATE principals SET active=0 WHERE principal_id='manager-1'",
        "UPDATE organizations SET policy_version=policy_version+1 WHERE org_id='org_demo'",
        "UPDATE manager_grants SET revoked_at='2026-10-14T00:00:00Z' WHERE grant_id='grant-01'",
    ],
)
def test_a_ticket_tool_requires_current_manager_grant_and_policy(db, mutation):
    con = open_existing(db)
    con.execute(mutation)
    con.close()
    before = protected_snapshot(db)
    with pytest.raises(AgentExecutionError):
        ReadOnlyAgentRunner(db, Scripted()).run("ep-01")
    con = open_existing(db)
    assert (
        con.execute(
            "SELECT count(*) FROM tool_events WHERE tool_status='SUCCESS'"
        ).fetchone()[0]
        == 0
    )
    assert con.execute("SELECT count(*) FROM candidate_requests").fetchone()[0] == 0
    con.close()
    assert protected_snapshot(db) == before
