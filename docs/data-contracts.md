# Data contracts and trust boundaries

The checked-in Python/Pydantic/SQLite definitions are canonical. Examples are schematic and omit identifiers/timestamps unless essential. Planned hosted APIs are intentionally not listed as implemented contracts.

| Contract | Required fields / constraints | Trust and versioning |
|---|---|---|
| A V1 candidate | `candidate_id`, `ticket_id`, closed `operation_hint`, optional order/recipient/content-class text, 1–8 `source_spans`, uncertainty flags and up to four alternate candidates. `Span` has source ID/version, integer start/end and 1–1024-char excerpt. `extra=forbid`, strict types. | Entire model output is untrusted; IDs/authority are rechecked against persisted episode. Defined in `trusted/contracts.py` and `agents/contracts.py`. Historical episodes remain V1-pinned. |
| A V2 interpretation | V1 hypothesis fields plus 1–8 `source_quotes`; each quote has source ID/version, lowercase 64-hex digest and 1–1024-char exact quotation. | Model quotation is stored separately; server binds it to actual A tool event and immutable source, derives offsets. New protocol is pinned per episode; no silent upgrade. |
| Policy result | Closed decision among `ALLOW`, `DENY`, `CLARIFY`, `REQUEST APPROVAL`, `INCOMPLETE EVIDENCE`; reason code and optional trusted facts/unsent clarification draft. | Trusted result; model cannot submit or override it. `ALLOW` is preliminary, not effect evidence. |
| Source snapshot | Ticket ID, source ID, source version, exact text and UTF-8 SHA-256 digest. | Customer-origin content remains untrusted even when source integrity is valid. Snapshots are immutable for an episode. |
| Tool call/event | Provider call ID, allowlisted tool name, exact JSON arguments; persisted event records role/invocation/episode, status, observation owner, sent turn and continuation turn. | Call ID comes from provider response; event/observation ownership and scope are trusted application facts. Never infer a successful observation from model prose. |
| B routine proposal | `status=PROPOSED`, closed purpose, closed template variant, nonempty selected allowlist of `order_id`, `order_status`, `customer_visible_eta`, and `observation_ref`. Strict/extra-forbidden. | Model selection is bounded; observation reference must correlate to successful B tool event, handoff and H2 verification. It remains a proposal. |
| B nonmaterial result | `ESCALATION_ACK`, `REFUSED`, or `UNABLE`; optional note <=280 chars. | No routine fields or claimed resource effect. B cannot change frozen escalation content. |
| Run evidence | Episode run-kind (`LIVE`, `RECORDED_REPLAY`, `DETERMINISTIC_COMPONENT_TEST`), pinned candidate protocol, separate invocation and correlated tool-event records. | The run label is application assigned; tests reject false LIVE attribution from scripted transports. Raw full model text is not assumed present. |
| Resource outcome | `NOT_ATTEMPTED`, `ACCEPTED_COMMITTED`, `EXPLICIT_POLICY_DENIAL`, `TECHNICAL_ERROR`, `INDETERMINATE`, `RECONCILED_COMMITTED`, `RECONCILED_NOT_COMMITTED`. | Only authoritative resource/journal and valid reconciliation establish these states. A model refusal/provider failure is not a policy-denial journal. |
| Offline evaluation result | Scenario ID, fixed script version, observed output/decision, expected branch judgment, protected-state comparison and zero-effect counters. Oracle truth is read after runtime is fixed. | `DETERMINISTIC_COMPONENT_TEST`; not model accuracy. See [`offline_scenarios.py`](../src/scoperelay/evaluation/offline_scenarios.py). |
| Local sandbox request | `RunInput` requires `mode`, `scenario_id`, `idempotency_key` (12–80 characters); optional `ticket_text` is capped at 600 characters; `run_kind` defaults to `DETERMINISTIC_COMPONENT_TEST`; extra fields are forbidden. | Browser supplies a request, but service validates mode/scope/session and owns resource/run identity. See [`sandbox/app.py`](../src/scoperelay/sandbox/app.py). |
| Sandbox error | Expected sandbox rejection is a JSON object such as `{"code":"SANDBOX_STORAGE_UNAVAILABLE"}` with an HTTP status; local-boundary rejections may be plain-text HTTP errors. | Do not treat HTTP status or an agent failure as a resource denial. Internal exception text is not returned as authority. |
| Browser request | Actual routes and request models are in `presentation/app.py` and `sandbox/app.py`; sandbox run input is a constrained Pydantic model. | No endpoint listed here should be interpreted as a hosted public API. Browser/session controls are local app safeguards, not enterprise IAM. |

## Valid versus invalid source example

For an immutable body `Please tell me the status of ORD-101.` with matching source version and digest, a V2 quotation selecting that exact unique passage can be bound to code-point offsets by trusted code. An excerpt with one altered character, the wrong version/digest, a repeated ambiguous passage, a source from another ticket, or an exact but irrelevant quote must fail or remain ineligible. Even a valid binding does not establish ownership or intent.

## B proposal shape example

Valid shape after a correlated successful B observation:

```json
{
  "status": "PROPOSED",
  "purpose": "STATUS_BRIEF",
  "template_variant": "STATUS_T1",
  "selected_field_ids": ["order_id", "order_status"],
  "observation_ref": "<server-recorded-tool-event-id>"
}
```

The placeholder is explanatory, not a production ID. A response adding `recipient`, `rendered_body`, `approved_by`, or an arbitrary field is invalid because the Pydantic contracts forbid extras. A well-shaped proposal with a forged or stale observation reference is still rejected by trusted correlation; a valid B proposal remains no more than a proposal.

## Compatibility

`V1_SPANS` and `V2_EXACT_QUOTE` are persisted episode protocols. Do not reinterpret old evidence under a new protocol. Migration changes belong in numbered SQLite migrations and their contract/security tests; no hosted API compatibility guarantee is claimed.

## Static showcase build contract

The static release has exactly eight allowlisted files, with names and SHA-256 values fixed in [`STATIC_SHOWCASE_RELEASE_MANIFEST.json`](delivery/STATIC_SHOWCASE_RELEASE_MANIFEST.json). Copied UTF-8 assets are normalized to LF before output. `data.json`'s `fixture_sha256` is SHA-256 over the fixture's UTF-8 bytes after LF newline normalization; `data.json` and `trace.json` are compact, sorted-key UTF-8 JSON with one fixed CRLF terminator to preserve the reviewed release bytes. The build rejects extra output entries and the verifier rejects missing, extra, or mismatched files. These byte-level conventions remove host checkout newline settings as an artifact input.
