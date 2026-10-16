"""OpenAI-compatible chat tool transport. No endpoint is contacted on import."""

from dataclasses import dataclass
from time import monotonic
from typing import Any, Protocol

from scoperelay.agents.contracts import (
    agent_a_v2_response_schema,
    agent_b_response_schema,
)


class LiveInferenceBlocked(RuntimeError):
    pass


class ProviderFailure(RuntimeError):
    pass


class SchemaRequestRejected(ProviderFailure):
    pass


@dataclass(frozen=True)
class ToolCall:
    call_id: str
    name: str
    arguments: str


@dataclass(frozen=True)
class ModelTurn:
    content: str | None
    tool_calls: tuple[ToolCall, ...]
    response_id: str | None = None
    model_id: str | None = None
    finish_reason: str | None = None
    usage: dict[str, int] | None = None
    refusal: str | None = None


class ChatTransport(Protocol):
    provider: str
    model: str
    run_kind: str

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        tool_name: str | None,
        max_tokens: int,
        response_protocol: str | None = None,
    ) -> ModelTurn: ...


class ProviderAdapter:
    """Explicit live opt-in; SDK retries disabled to keep request evidence honest."""

    run_kind = "LIVE"

    def __init__(
        self,
        *,
        provider: str = "",
        model: str = "",
        base_url: str = "",
        api_key: str = "",
        authorized: bool = False,
        timeout: float = 20.0,
        max_requests: int | None = None,
    ) -> None:
        if not authorized or not provider or not model or not base_url or not api_key:
            raise LiveInferenceBlocked(
                "LIVE_INFERENCE_BLOCKED: explicit entitlement, config and cost approval required"
            )
        if max_requests is not None and not 1 <= max_requests <= 6:
            raise LiveInferenceBlocked("invalid LIVE generation request ceiling")
        if not base_url.startswith("https://") and not base_url.startswith(
            ("http://localhost:", "http://127.0.0.1:")
        ):
            raise LiveInferenceBlocked(
                "provider endpoint must be trusted HTTPS or local loopback"
            )
        if provider == "ollama-local" and base_url.rstrip("/") not in {
            "http://localhost:11434/v1",
            "http://localhost:11435/v1",
            "http://127.0.0.1:11434/v1",
            "http://127.0.0.1:11435/v1",
        }:
            raise LiveInferenceBlocked(
                "ollama-local requires an approved local Ollama v1 endpoint"
            )
        if provider == "ollama-local" and model != "gemma4:e4b":
            raise LiveInferenceBlocked(
                "ollama-local requires the approved installed model"
            )
        if provider == "groq" and (
            base_url.rstrip("/") != "https://api.groq.com/openai/v1"
            or model != "openai/gpt-oss-20b"
        ):
            raise LiveInferenceBlocked(
                "Groq candidate endpoint/model must be fixed by trusted configuration"
            )
        from openai import OpenAI

        self.provider = provider
        self.model = model
        self.max_requests = max_requests
        self.request_count = 0
        self.request_seconds: list[float] = []
        self._client = OpenAI(
            api_key=api_key, base_url=base_url, timeout=timeout, max_retries=0
        )

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        tool_name: str | None,
        max_tokens: int,
        response_protocol: str | None = None,
    ) -> ModelTurn:
        if tool_name not in {
            None,
            "read_assigned_ticket",
            "read_verified_order_status",
        }:
            raise ProviderFailure("unsupported tool declaration")
        continuation = bool(messages and messages[-1].get("role") == "tool")
        if response_protocol is not None and not (
            response_protocol == "V2_EXACT_QUOTE"
            and self.provider in {"ollama-local", "groq"}
            and continuation
            and tool_name == "read_assigned_ticket"
        ):
            raise ProviderFailure("unsupported structured-output protocol or turn")
        tools = None
        if tool_name is not None:
            argument = (
                "ticket_id"
                if tool_name == "read_assigned_ticket"
                else "verification_id"
            )
            tools = [
                {
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "description": "Read only the server-scoped synthetic record.",
                        "parameters": {
                            "type": "object",
                            "properties": {argument: {"type": "string"}},
                            "required": [argument],
                            "additionalProperties": False,
                        },
                    },
                }
            ]
        try:
            options: dict[str, Any] = {"model": self.model, "messages": messages}
            if self.provider == "ollama-local":
                # Ollama's documented Chat Completions subset uses max_tokens;
                # store and parallel_tool_calls are not documented request fields.
                options["max_tokens"] = max_tokens
                options["reasoning_effort"] = "none"
                if response_protocol == "V2_EXACT_QUOTE":
                    options["response_format"] = {
                        "type": "json_schema",
                        "json_schema": {
                            "name": "candidate_interpretation_v2_or_failure",
                            "schema": agent_a_v2_response_schema(),
                            "strict": True,
                        },
                    }
                elif continuation or tool_name is None:
                    # Ollama documents JSON mode on /v1/chat/completions. The
                    # native tool turn remains unconstrained; only a final
                    # content turn requests JSON formatting.
                    options["response_format"] = {"type": "json_object"}
            elif self.provider == "groq":
                # Offline request-construction readiness only. Groq documents
                # tool calls and structured output as separate requests. The
                # current union schema is not strict-mode compatible (optional
                # AgentAFailure field; open other_candidates objects), so the
                # server's own V2/Pydantic+source checks remain mandatory.
                options["max_completion_tokens"] = max_tokens
                if response_protocol == "V2_EXACT_QUOTE":
                    options["response_format"] = {
                        "type": "json_schema",
                        "json_schema": {
                            "name": "candidate_interpretation_v2_or_failure",
                            "schema": agent_a_v2_response_schema(),
                            "strict": False,
                        },
                    }
                elif continuation and tool_name == "read_verified_order_status":
                    options["response_format"] = {
                        "type": "json_schema",
                        "json_schema": {
                            "name": "agent_b_proposal_or_nonmaterial",
                            "schema": agent_b_response_schema(),
                            "strict": False,
                        },
                    }
                elif continuation or tool_name is None:
                    options["response_format"] = {"type": "json_object"}
            else:
                options.update(max_completion_tokens=max_tokens, store=False)
            if tools is not None and not continuation:
                options.update(tools=tools, tool_choice="auto")
                if self.provider != "ollama-local":
                    options["parallel_tool_calls"] = False
            if (
                getattr(self, "max_requests", None) is not None
                and self.request_count >= self.max_requests
            ):
                raise ProviderFailure("generation request ceiling reached")
            self.request_count = getattr(self, "request_count", 0) + 1
            started = monotonic()
            try:
                response = self._client.chat.completions.create(**options)
            finally:
                self.request_seconds = getattr(self, "request_seconds", []) + [
                    monotonic() - started
                ]
            if len(response.choices) != 1:
                raise ProviderFailure("unexpected provider choice count")
            choice = response.choices[0]
            if choice.message is None:
                raise ProviderFailure("missing provider message")
            if (
                choice.message.content is not None
                and type(choice.message.content) is not str
            ):
                raise ProviderFailure("unexpected provider content shape")
            calls = tuple(
                ToolCall(c.id, c.function.name, c.function.arguments)
                for c in (choice.message.tool_calls or ())
                if c.type == "function"
            )
            if len(calls) != len(choice.message.tool_calls or ()):
                raise ProviderFailure("unsupported provider tool call type")
            if any(
                type(call.call_id) is not str
                or not call.call_id
                or type(call.name) is not str
                or not call.name
                or type(call.arguments) is not str
                for call in calls
            ):
                raise ProviderFailure("invalid native tool call envelope")
            usage = response.usage
            return ModelTurn(
                choice.message.content,
                calls,
                response.id or None,
                response.model or None,
                choice.finish_reason,
                {
                    "prompt_tokens": usage.prompt_tokens,
                    "completion_tokens": usage.completion_tokens,
                }
                if usage
                else None,
                getattr(choice.message, "refusal", None),
            )
        except ProviderFailure:
            raise
        except Exception as exc:
            # SDK exceptions may contain endpoint, key or payload; never log them.
            if (
                response_protocol is not None
                and type(exc).__name__ == "BadRequestError"
            ):
                raise SchemaRequestRejected("SCHEMA_REQUEST_REJECTED") from None
            raise ProviderFailure(type(exc).__name__) from None
