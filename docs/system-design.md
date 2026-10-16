# System design and tradeoffs

## Goals

- Make authority and policy decisions in trusted application code, never infer them from a model role label or explanation.
- Preserve exact source, tool-call, tool-result, continuation and decision correlations.
- Keep the runnable dataset synthetic and the public showcase static.
- Separate ordinary proposal evidence from a protected resource commit.
- Make local reproduction possible from a locked Python 3.13 environment.

## Design choices

The implementation uses bounded explicit Python orchestration instead of a general agent graph framework; SQLite instead of an external database; Pydantic strict contracts for model-facing data; and a separate read dispatcher for each agent role. This keeps the exercised control flow inspectable, local and reproducible without a hosted dependency, and avoids granting either model a general-purpose tool surface. A framework could reduce custom orchestration code, but would add an execution layer whose security semantics this narrow project does not need to delegate. A hosted database could support multi-instance service, but no distributed quota, identity, recovery or no-effect isolation contract is approved. These are implementation choices, not claims that the code is an OS-level sandbox.

Model output is data, not a command language. A and B have separate prompts, invocation records, tool scopes, conversation histories, and continuation checks. Trusted state is queried independently. Source text can contain adversarial instructions, but those instructions do not become system authority.

## State ownership

SQLite owns synthetic organizations, policy/grants, tickets/source snapshots, customer/account/order/contact records, episodes, invocation/tool events, bindings, decisions, handoffs, approvals, material, resource attempts/effects/journal, and evaluation records. Oracle labels are read after runtime evidence is fixed and are not passed to either agent. The static showcase is generated from a curated public payload and is not a live view of the local DB.

The trusted resource is the only queue/effect admission owner. The read-only A/B coordinator intentionally does not import it. The local supervisor service is an internal simulation and its module documentation explicitly says HTTP UI authentication/CSRF is a later milestone; do not interpret its existence as production approval workflow.

## Lifecycle and idempotency

The implementation records generation request counts before dispatch and disables SDK retries. Each episode has a pinned protocol and a unique invocation/evidence lifecycle. Protected resource paths use stable action identity and business-operation identity; current state is rechecked under writer serialization. A timeout or lost acknowledgment can leave the outcome unknown and recovery is constrained to original evidence/identity.

SQLite uses explicit immediate transactions in `storage/transaction.py` and a process/application writer-ownership and continuity mechanism. These mechanisms target a single local application and filesystem. They do not provide distributed locks, multi-host consistency, or a hosted quota service.

## Session isolation and idempotency

The local sandbox maintains owner-scoped session state and per-run synthetic databases under its configured local data root, uses a single-instance lease, caps runs/turns/sessions locally, and checks ownership on public-facing service methods. These are local prototype controls; they do not provide identity proof or globally coordinated quotas. The stable idempotency key prevents repeating the same local run request; provider requests are counted/reserved before dispatch and SDK retries are disabled. Resource-layer action identity and business-operation identity are separately enforced for protected synthetic admission. A failed run is not automatically retried under a new business identity.

## Failure semantics

Malformed JSON/schema, invalid native tool envelopes, wrong scope, failed continuation, source mismatch, provider errors, stale grants and technical DB failures remain distinct failure/evidence states. A provider timeout is not a trusted policy denial. A proposed `ALLOW` is not resource acceptance. A possible commit with lost acknowledgment is not safely labeled absent without the recovery witness checks.

Known limit: `tests/security/test_writer_lifecycle_04e.py` retains one xfailed coherent-all-state rollback case. The codebase must not claim protected-effect release readiness until that guarantee is resolved and reverified.

## Resource and cost boundaries

Offline tests/scenarios require no provider credential and make no inference request. The live provider adapter requires explicit authorization/configuration, endpoint/model checks, caps requests (where configured) and has SDK retries disabled. Hosted provider rights, billing, privacy, global quotas, abuse resistance and multi-instance deployment are not established. CI sets the local-live authorization environment to `NO`, makes no model requests, and has no deployment step.

## Observability and operations

The app persists invocation IDs, tool call IDs/events, observations, schema/binding diagnostics, decisions, handoffs and resource evidence while redacting model text from failure diagnostics. The historical run report intentionally contains correlation and hash evidence but not full raw continuation text. This is not a claim of signed traces, centralized telemetry, or production monitoring.
