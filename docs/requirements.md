# Requirements and evidence map

These requirements describe the present synthetic implementation, not a production service. “Verified locally” refers only to the linked tests/checks; it is not a security certification. The canonical IDs and trust boundaries in the [S5 architecture](architecture/S5_CANONICAL_ARCHITECTURE.md) remain authoritative.

| ID | Requirement / acceptance evidence | Implementation and tests | Status |
|---|---|---|---|
| FR-001 | A and B are separate bounded roles with separate invocations and conversation continuations. | [`read_only_agents.py`](../src/scoperelay/application/read_only_agents.py); `tests/integration/test_read_only_agents_05a.py` | Implemented; historical live SC-01 evidence is report-backed |
| FR-002 | Only scoped ticket and verified-order read tools are model exposed. | [`tool_dispatcher.py`](../src/scoperelay/trusted/tool_dispatcher.py); `test_read_only_agents_05a.py` | Locally tested |
| FR-003 | Workflow decisions are deterministic trusted outcomes, not model assertions. | [`authorization.py`](../src/scoperelay/trusted/authorization.py); `tests/security/test_trusted_authorization.py` | Locally tested |
| FR-004 | V2 quotation selections bind to one exact immutable source occurrence; server derives offsets. | [`quote_binding.py`](../src/scoperelay/trusted/quote_binding.py), [`source_spans.py`](../src/scoperelay/storage/source_spans.py); `tests/contracts/test_source_quote_binding_07d.py`, `tests/contracts/test_quote_binding_07e.py` | Locally tested; occurrence/relevance is not intent proof |
| FR-005 | Reviewer pages read persisted synthetic state and distinguish proposal from resource evidence. | [`read_models.py`](../src/scoperelay/presentation/read_models.py), [`app.py`](../src/scoperelay/presentation/app.py); integration/security tests | Implemented for local use |
| FR-006 | The curated public static build reproduces the frozen eight-file artifact. | [`public_showcase.py`](../src/scoperelay/evaluation/public_showcase.py), [`verify_static.py`](../scripts/verify_static.py), [Task 10 manifest](delivery/STATIC_SHOWCASE_RELEASE_MANIFEST.json) | Fresh frozen-archive and clean curated-source builds both match 8/8 under Python 3.13.16; newline normalization is regression-tested. |
| SEC-001 | Model/customer text cannot mint grants, identities, policy, recipient, supervisor authority, or business identity. | `trusted/authorization.py`, `trusted/approval.py`; `tests/security/`, `tests/integration/test_read_only_agents_05a.py` | Regression-tested; not exhaustive |
| SEC-002 | Every agent tool dispatch is role- and episode-scoped and rejects extra arguments/tools. | `trusted/tool_dispatcher.py`; integration tests | Regression-tested |
| SEC-003 | Source evidence is checked against the immutable source body, digest, version, episode and actual A tool observation. | `trusted/quote_binding.py`, `storage/source_spans.py`; contract/integration tests | Regression-tested |
| SEC-004 | Routine output is rendered from trusted allowlisted fields; model cannot choose arbitrary content or recipient. | `trusted/renderer.py`, `trusted/resource.py`; `tests/security/test_protected_resource.py` | Code/tests exist; public effect remains disabled |
| SEC-005 | Internal escalation requires frozen material and a separate supervisor approval; B acknowledgment is nonmaterial. | `trusted/approval.py`, `trusted/material_store.py`, `trusted/resource.py`; approval/resource tests | Local simulation only; no public effect |
| REL-001 | Unknown commit status is not equated with denial or noncommit; same original identity is reconciled. | `storage/continuity.py`, `storage/writer_owner.py`, `storage/restore.py`, `trusted/resource.py`; `tests/security/test_recovery*` | Partially implemented; known coherent-rollback xfail remains |
| REL-002 | SQLite admission writes are serialized and commit with explicit transaction control. | `storage/transaction.py`, `trusted/resource.py`; transaction/resource tests | Local regression-tested; not a distributed system |
| EV-001 | Historical replay, deterministic evaluation, and new live inference have distinct labels and evidence. | `evaluation/trace_manifest.py`, `offline_scenarios.py`, `inference/adapter.py`; fixture/evidence tests | Implemented; historical live evidence only for SC-01 |
| REP-001 | Locked Python 3.13 environment and local checks reproduce without model credentials. | `pyproject.toml`, `uv.lock`, [development guide](development.md), CI workflow | Local checks pass; hosted CI not run |
| A11Y-001 | Static showcase and local UI receive viewport/browser checks. | `tests/browser_qa/`; [verification record](evidence/ENGINEERING_VERIFICATION.md) | Existing Edge QA across five widths; not a formal accessibility audit |
| NFR-002 | No latency, throughput, availability, or production SLA is claimed without a repeatable benchmark/service. | No benchmark/SLA module or result is present | Explicitly not measured |

## Closed workflow vocabulary

The canonical design limits protected-operation classes to `ROUTINE_ORDER_STATUS` and `SANITIZED_INTERNAL_ESCALATION`; it defines workflow decisions `ALLOW`, `DENY`, `CLARIFY`, `REQUEST APPROVAL`, and `INCOMPLETE EVIDENCE`. Resource outcomes are separately recorded as `NOT_ATTEMPTED`, `ACCEPTED_COMMITTED`, `EXPLICIT_POLICY_DENIAL`, `TECHNICAL_ERROR`, `INDETERMINATE`, `RECONCILED_COMMITTED`, and `RECONCILED_NOT_COMMITTED`. A decision is not a resource outcome. `CLARIFY` remains an unsent draft/pending state in this scope.

## Explicit exclusions

No real customer support, external delivery, public inference endpoint, compulsory hosted service, multi-tenant production security, public protected action, performance SLA, or guarantee of semantic intent is provided. Proposed Live AI work is tracked separately and is not accepted as shipped functionality.

## Acceptance summary

At this candidate revision, the local suite is 420 passed / 1 known xfailed / 1 warning; Ruff, format, compile, locked dependency, Markdown link, and fresh source-to-artifact checks passed as recorded in [engineering verification](evidence/ENGINEERING_VERIFICATION.md). Current hosted status is available in GitHub Actions; local results do not establish deployment behavior.
