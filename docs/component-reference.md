# Component and function reference

This is a navigation guide to significant implementation points, not a promise of a stable third-party Python API. All symbols are internal unless separately exported and versioned.

| Subsystem | Important symbol | Contract / side effects | Exercising tests |
|---|---|---|---|
| A/B coordinator | [`ReadOnlyAgentRunner.run`](../src/scoperelay/application/read_only_agents.py) | Accepts a persisted episode plus optional trusted escalation context; persists invocation/tool/binding/decision/handoff/proposal evidence. It does not call the protected resource. | `tests/integration/test_read_only_agents_05a.py`, `tests/unit/test_local_live_gate.py` |
| A/B coordinator | `ReadOnlyAgentRunner._tool_loop` | Internal bounded provider-turn loop; persists real tool calls and returns actual scoped observations to the same conversation; enforces duration/token/call constraints. | `test_read_only_agents_05a.py`, `tests/contracts/test_structured_output_07f1.py` |
| Provider | [`ProviderAdapter.complete`](../src/scoperelay/inference/adapter.py) | One OpenAI-compatible completion request; `max_retries=0`, fixed tool allowlist, request ceiling when configured. Raises typed provider failures; does not make policy decisions. | Adapter/wire tests in `test_read_only_agents_05a.py`, `test_structured_output_07f1.py` |
| Strict contracts | [`CandidateInterpretationV2`, `RoutineProposal`, `agent_a_v2_response_schema`, `agent_b_response_schema`](../src/scoperelay/agents/contracts.py) | Strict Pydantic models, extra fields forbidden; Agent A supports source quotation choices; Agent B supports bounded routine or nonmaterial status. | `tests/contracts/test_structured_output_07f1.py`, source-binding tests |
| Dispatcher | [`dispatch`](../src/scoperelay/trusted/tool_dispatcher.py) | Role/scope-bound read only; requires exact tool name, call ID and sole expected argument. | `test_read_only_agents_05a.py` |
| Source | [`source_digest`, `resolve_span`](../src/scoperelay/storage/source_spans.py) | Validates UTF-8 SHA-256, source identity/version, code-point offsets and exact excerpt. No DB mutation. | `tests/unit/test_source_spans.py` |
| V2 evidence | [`bind_exact_quotes`](../src/scoperelay/trusted/quote_binding.py) | Correlates successful A tool event/continuation and observed pinned source; uniquely binds exact quotations and derives offsets. | `tests/contracts/test_source_quote_binding_07d.py`, `test_quote_binding_07e.py` |
| Authorization | [`evaluate`](../src/scoperelay/trusted/authorization.py) | Reads trusted DB state and candidate hypothesis; returns a closed eligibility result. Preliminary decision only; does not submit an effect. | `tests/security/test_trusted_authorization.py` |
| Handoff | [`make_handoff_preview`](../src/scoperelay/trusted/handoff.py) | Builds H1/H2 trusted references and segregates H3/H4 lower-trust content. | `test_read_only_agents_05a.py` |
| Rendering | [`canonical_bytes`, `render_routine`, `render_escalation`](../src/scoperelay/trusted/renderer.py) | NFC/canonical digest and fixed templates over verified facts and closed field IDs. | `tests/security/test_protected_resource.py` |
| Approval | [`LocalSupervisorService`](../src/scoperelay/trusted/approval.py) | Local synthetic supervisor simulation; digest-bound approval lifecycle. The service is not exposed as a production authentication flow. | `tests/security/test_approval_04c.py`, `test_trusted_authorization.py` |
| Resource | [`ProtectedCommunicationResource`](../src/scoperelay/trusted/resource.py) | Sole synthetic protected admission API; current-state checks, identities, approval, serialized writes and journal/effect outcomes. Not agent-accessible or public-showcase-accessible. | `tests/security/test_protected_resource.py`, recovery tests |
| Storage | [`initialize`](../src/scoperelay/storage/schema.py), [`immediate`](../src/scoperelay/storage/transaction.py), [`writer_owner`](../src/scoperelay/storage/writer_owner.py), [`stage_restore`](../src/scoperelay/storage/restore.py) | Schema/migration checks, explicit immediate transaction, one application writer owner, guarded staged restore/reconciliation. Local SQLite only. | `tests/contracts/test_data_foundation.py`, `tests/integration/test_sqlite_transaction.py`, `tests/security/test_recovery*` |
| Offline evaluation | [`evaluate_scenario`, `evaluate_all`](../src/scoperelay/evaluation/offline_scenarios.py) | Runs scripted turns in disposable DBs and compares against isolated oracle judgments after runtime. No provider call. | `tests/unit/test_offline_evaluation_08a.py` |
| Static payload | [`public_payload`, `build`](../src/scoperelay/evaluation/public_showcase.py) | Canonicalizes UTF-8 source/fixture newlines, emits stable JSON and builds the eight-file static payload; checked against the unchanged frozen manifest. | `tests/integration/test_showcase_08b.py`, `test_interactive_harness.py` |
| Reviewer UI | [`create_app`](../src/scoperelay/presentation/app.py), read models in [`read_models.py`](../src/scoperelay/presentation/read_models.py) | Local FastAPI routes and read-only projections; no direct presentation-layer effect SQL. | `tests/integration/test_workbench_06a.py`, browser QA scripts |
| Public sandbox | [`create_sandbox_app`](../src/scoperelay/sandbox/app.py), [`SandboxService`](../src/scoperelay/sandbox/service.py) | Local opt-in synthetic single-instance sandbox; configured scripted offline transport by default; explicit provider gate for live path. This is not hosted. | `tests/integration/test_sandbox_10a.py` through `10d.py`, unit live-gate tests |

For field-level contracts, trust origin and failure behavior, see [data contracts](data-contracts.md). For the actual transition ordering, see [execution lifecycle](execution-lifecycle.md).

## Core signatures

The signatures below were introspected from the candidate's Python 3.13 environment; paths link to their defining modules. They are implementation interfaces, not a promise of backwards compatibility.

```python
ReadOnlyAgentRunner(db_path: Path, transport: ChatTransport, *, max_tokens: int = 768,
                    max_duration_seconds: float = 90) -> None
ReadOnlyAgentRunner.run(episode_id: str, *, trusted_escalation_reason: str | None = None,
                        material_action_id: str | None = None,
                        approval_id: str | None = None) -> ReadOnlyResult
ProviderAdapter(*, provider: str = "", model: str = "", base_url: str = "",
                api_key: str = "", authorized: bool = False, timeout: float = 20.0,
                max_requests: int | None = None) -> None
ProviderAdapter.complete(messages: list[dict[str, Any]], *, tool_name: str | None,
                         max_tokens: int, response_protocol: str | None = None) -> ModelTurn
dispatch(con: sqlite3.Connection, scope: ToolScope, call: ToolCall) -> dict[str, object]
source_digest(body_text: str) -> str
resolve_span(*, expected_ticket_id: str, expected_source_version: int, ticket_id: str,
             source_version: int, body_text: str, body_sha256: str, start: int,
             end: int, excerpt: str) -> str
bind_exact_quotes(con: sqlite3.Connection, *, episode_id: str, invocation_id: str,
                  tool_event_id: str,
                  candidate: CandidateInterpretationV2) -> list[dict[str, object]]
evaluate(con: sqlite3.Connection, task: TrustedTask, candidate: CandidateRequestV1,
         *, at: datetime) -> EligibilityResult
ProtectedCommunicationResource(database_path: Path,
                               owner: ApplicationWriter | None = None) -> None
initialize(path: Path, *, source_override: str | None = None) -> None
connect(path: Path, *, timeout: float = 0.2) -> sqlite3.Connection
immediate(con: sqlite3.Connection) -> Iterator[sqlite3.Connection]
evaluate_scenario(scenario_id: str) -> ScenarioRecord
evaluate_all() -> dict
create_sandbox_app(root: Path, *, enabled: bool = False,
                    daily_turn_budget: int = 40, session_ttl: int = 1800,
                    cleanup_interval: float = 60, invite_digest: str | None = None,
                    local_live_enabled: bool = False) -> FastAPI
create_app(database_path: Path) -> FastAPI
public_payload() -> dict
build(destination: Path = PUBLIC_OUTPUT) -> Path
```

**Failure and side-effect notes.** `run` can raise `AgentExecutionError` and persists model/tool/decision evidence as it advances; it does not invoke a protected effect. `ProviderAdapter` raises `LiveInferenceBlocked`, `ProviderFailure` or `SchemaRequestRejected`; construction and each completion are live-gated, and completion may contact the configured provider. `dispatch`, `resolve_span`, `bind_exact_quotes`, `evaluate`, and renderers raise typed validation/scope errors on invalid input; authorization reads DB state but does not mutate a resource. `ProtectedCommunicationResource` is the sole protected synthetic writer and can persist attempts/effects/journals; do not call it from agent/UI/public-showcase paths. `evaluate_scenario` creates and disposes a temporary DB and uses scripted turns. `build` writes generated static files to its destination.
