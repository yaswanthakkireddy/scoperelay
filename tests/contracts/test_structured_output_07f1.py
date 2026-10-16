"""Offline wire/schema tests only. No Ollama generation or network request."""

from types import SimpleNamespace
import json

import httpx
import pytest
from openai import OpenAI

from scoperelay.agents.contracts import (
    AgentAFailure,
    CandidateInterpretationV2,
    agent_a_v2_response_schema,
)
from scoperelay.inference.adapter import (
    ProviderAdapter,
    ProviderFailure,
    SchemaRequestRejected,
)


def _fake_adapter(create):
    adapter = object.__new__(ProviderAdapter)
    adapter.provider, adapter.model = "ollama-local", "gemma4:e4b"
    adapter.max_requests, adapter.request_count, adapter.request_seconds = 6, 0, []
    adapter._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    return adapter


def _response(content, *, tool_calls=(), reason="stop"):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=content, tool_calls=list(tool_calls), refusal=None
                ),
                finish_reason=reason,
            )
        ],
        id="synthetic-provider-response",
        model="gemma4:e4b",
        usage=None,
    )


def test_authoritative_v2_union_schema_preserves_required_nested_object_and_failure():
    schema = agent_a_v2_response_schema()
    assert len(schema["anyOf"]) == 2
    success = next(
        item
        for item in schema["anyOf"]
        if item.get("$ref", "").endswith("CandidateInterpretationV2")
    )
    failure = next(
        item
        for item in schema["anyOf"]
        if item.get("$ref", "").endswith("AgentAFailure")
    )
    candidate = schema["$defs"][success["$ref"].split("/")[-1]]
    refusal = schema["$defs"][failure["$ref"].split("/")[-1]]
    uncertainty = schema["$defs"]["Uncertainty"]
    assert candidate["additionalProperties"] is False
    assert "uncertainty" in candidate["required"]
    assert candidate["properties"]["uncertainty"] == {"$ref": "#/$defs/Uncertainty"}
    assert (
        uncertainty["type"] == "object" and uncertainty["additionalProperties"] is False
    )
    assert set(uncertainty["required"]) == {
        "ambiguous",
        "quoted_only",
        "conflicting_references",
    }
    assert uncertainty["properties"]["ambiguous"]["type"] == "boolean"
    assert uncertainty["properties"]["quoted_only"]["type"] == "boolean"
    assert (
        uncertainty["properties"]["conflicting_references"]["items"]["type"] == "string"
    )
    assert "source_quotes" in candidate["required"]
    assert candidate["properties"]["source_quotes"]["minItems"] == 1
    assert "source_spans" not in candidate["properties"]
    assert refusal["required"] == ["status"]
    assert refusal["properties"]["status"]["enum"] == ["REFUSED", "UNABLE"]
    assert "uncertainty" not in refusal["properties"]
    assert AgentAFailure.model_validate({"status": "UNABLE"}).status == "UNABLE"
    with pytest.raises(Exception):
        CandidateInterpretationV2.model_validate({"uncertainty": "unknown"})


def test_native_tool_turn_unformatted_final_turn_uses_canonical_schema():
    captured = []
    call = SimpleNamespace(
        type="function",
        id="call-1",
        function=SimpleNamespace(
            name="read_assigned_ticket", arguments='{"ticket_id":"T-001"}'
        ),
    )

    def create(**kwargs):
        captured.append(kwargs)
        return (
            _response(None, tool_calls=(call,), reason="tool_calls")
            if len(captured) == 1
            else _response('{"status":"UNABLE"}')
        )

    adapter = _fake_adapter(create)
    first = adapter.complete(
        [
            {"role": "system", "content": "synthetic"},
            {"role": "user", "content": "read"},
        ],
        tool_name="read_assigned_ticket",
        max_tokens=256,
    )
    assert first.tool_calls[0].call_id == "call-1"
    assert captured[0]["tool_choice"] == "auto"
    assert "response_format" not in captured[0]
    feedback = [
        {"role": "system", "content": "synthetic"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {
                        "name": "read_assigned_ticket",
                        "arguments": '{"ticket_id":"T-001"}',
                    },
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call-1", "content": "synthetic observation"},
    ]
    second = adapter.complete(
        feedback,
        tool_name="read_assigned_ticket",
        max_tokens=256,
        response_protocol="V2_EXACT_QUOTE",
    )
    assert second.content == '{"status":"UNABLE"}'
    assert captured[1]["messages"] == feedback
    assert captured[1]["response_format"] == {
        "type": "json_schema",
        "json_schema": {
            "name": "candidate_interpretation_v2_or_failure",
            "schema": agent_a_v2_response_schema(),
            "strict": True,
        },
    }
    assert "tools" not in captured[1] and "tool_choice" not in captured[1]
    assert adapter.request_count == 2


def test_schema_request_only_on_approved_local_a_continuation():
    adapter = _fake_adapter(lambda **_: pytest.fail("must reject before dispatch"))
    for messages, tool, protocol in [
        (
            [{"role": "user", "content": "initial"}],
            "read_assigned_ticket",
            "V2_EXACT_QUOTE",
        ),
        (
            [{"role": "tool", "content": "observation"}],
            "read_verified_order_status",
            "V2_EXACT_QUOTE",
        ),
        (
            [{"role": "tool", "content": "observation"}],
            "read_assigned_ticket",
            "UNKNOWN",
        ),
    ]:
        with pytest.raises(ProviderFailure, match="unsupported structured-output"):
            adapter.complete(
                messages, tool_name=tool, max_tokens=256, response_protocol=protocol
            )
    assert adapter.request_count == 0


def test_schema_http_rejection_is_redacted_counted_and_never_falls_back():
    bad_request = type("BadRequestError", (Exception,), {})
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        raise bad_request("synthetic secret or raw payload must not escape")

    adapter = _fake_adapter(create)
    with pytest.raises(
        SchemaRequestRejected, match="SCHEMA_REQUEST_REJECTED"
    ) as caught:
        adapter.complete(
            [{"role": "tool", "content": "observation"}],
            tool_name="read_assigned_ticket",
            max_tokens=256,
            response_protocol="V2_EXACT_QUOTE",
        )
    assert "secret" not in str(caught.value)
    assert len(calls) == adapter.request_count == 1
    assert len(adapter.request_seconds) == 1


def test_provider_response_does_not_override_trusted_schema_validation():
    adapter = _fake_adapter(lambda **_: _response('{"uncertainty":"unsure"}'))
    turn = adapter.complete(
        [{"role": "tool", "content": "observation"}],
        tool_name="read_assigned_ticket",
        max_tokens=256,
        response_protocol="V2_EXACT_QUOTE",
    )
    assert turn.content == '{"uncertainty":"unsure"}'
    with pytest.raises(Exception):
        CandidateInterpretationV2.model_validate_json(turn.content)


@pytest.mark.parametrize("reject", [False, True])
def test_installed_sdk_serializes_schema_over_mock_http_only(reject):
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        if reject:
            return httpx.Response(
                400,
                json={
                    "error": {
                        "message": "synthetic rejection",
                        "type": "invalid_request_error",
                    }
                },
            )
        return httpx.Response(
            200,
            json={
                "id": "mock-only",
                "object": "chat.completion",
                "created": 0,
                "model": "gemma4:e4b",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": '{"status":"UNABLE"}',
                        },
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    adapter = _fake_adapter(lambda **_: pytest.fail("SDK mock replaces this client"))
    adapter._client = OpenAI(
        api_key="ollama",
        base_url="http://127.0.0.1:11435/v1",
        http_client=httpx.Client(transport=httpx.MockTransport(respond)),
        max_retries=0,
    )
    if reject:
        with pytest.raises(SchemaRequestRejected):
            adapter.complete(
                [
                    {
                        "role": "tool",
                        "tool_call_id": "call-1",
                        "content": "synthetic observation",
                    }
                ],
                tool_name="read_assigned_ticket",
                max_tokens=256,
                response_protocol="V2_EXACT_QUOTE",
            )
    else:
        turn = adapter.complete(
            [
                {
                    "role": "tool",
                    "tool_call_id": "call-1",
                    "content": "synthetic observation",
                }
            ],
            tool_name="read_assigned_ticket",
            max_tokens=256,
            response_protocol="V2_EXACT_QUOTE",
        )
        assert turn.content == '{"status":"UNABLE"}'
    assert len(requests) == adapter.request_count == 1
    assert requests[0]["response_format"]["type"] == "json_schema"
    assert (
        requests[0]["response_format"]["json_schema"]["schema"]
        == agent_a_v2_response_schema()
    )
    assert "tools" not in requests[0]
