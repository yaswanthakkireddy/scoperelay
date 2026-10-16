# Implemented algorithms

This index explains algorithms present in the source, not every planned policy idea. Complexity statements describe local bounded work only; they exclude model/provider/network time.

## 1. Role-scoped tool admission

**Source:** [`trusted/tool_dispatcher.py::dispatch`](../src/scoperelay/trusted/tool_dispatcher.py), [`storage/agent_reads.py`](../src/scoperelay/storage/agent_reads.py). **Tests:** `tests/integration/test_read_only_agents_05a.py`.

Given server-owned role/episode/grant/ticket/handoff/verification scope and a provider tool call, choose the one permitted tool name and one expected argument key; parse arguments; require an exact one-key object, string value, nonempty call ID, and equality with the trusted bound ID; dispatch through the repository's current-record checks. Return a typed, bounded observation. For argument length `n`, JSON parsing and validation are O(n); DB lookup cost depends on SQLite indexes.

```text
expected_tool, expected_key, trusted_id = scope_for(role)
if call.name != expected_tool or call.id is empty: reject
args = parse_json(call.arguments)
if keys(args) != {expected_key} or args[expected_key] != trusted_id: reject
return repository.read_using_server_scope(scope)
```

Unknown tools, extra/nested arguments, another ticket/order, missing scope, inactive grant or oversized source fail. Security property: a model-selected identifier narrows neither authority nor tenant scope.

## 2. Immutable source span verification

**Source:** [`storage/source_spans.py::source_digest`, `resolve_span`](../src/scoperelay/storage/source_spans.py). **Tests:** `tests/unit/test_source_spans.py`.

Check expected ticket/version, hash of exact UTF-8 source text, integer code-point offsets with `0 <= start < end <= len(body)`, and exact equality between the selected slice and excerpt. The hash is O(n) in source bytes; slicing/comparison is O(k) in excerpt length. Python string indexing uses code points, not UTF-8 byte offsets.

V1 carries model-authored spans and trusted code verifies them. In V2, [`trusted/quote_binding.py::bind_exact_quotes`](../src/scoperelay/trusted/quote_binding.py) finds the selected quote in the actual tool-observed pinned body, requires exactly one occurrence, runs conservative necessary-support checks, then derives offsets and calls `resolve_span`. Search is O(n) per quote; with at most eight quotations of at most 1024 characters, it is bounded by the schema and source size limit. Exact presence is not intent, ownership, grant, recipient eligibility, or approval.

## 3. Deterministic authorization evaluation

**Source:** [`trusted/authorization.py::evaluate`, `_evaluate`](../src/scoperelay/trusted/authorization.py). **Tests:** `tests/security/test_trusted_authorization.py`, `tests/integration/test_read_only_agents_05a.py`.

Read server-owned episode, ticket, org, assignment, policy and grant; reject stale versions, inactive entities, invalid issuer/scope, revoked/expired grant, unpermitted operation and forbidden content; detect uncertainty/contradictory references; for routine status independently resolve order ownership/current state and unique verified customer contact; for internal escalation require a trusted escalation reason and verified internal synthetic contact. Return one of `ALLOW`, `DENY`, `CLARIFY`, `REQUEST APPROVAL`, `INCOMPLETE EVIDENCE` with typed reason and (only when eligible) verified facts. No resource attempt is made by this function.

The implementation uses a bounded sequence of indexed database lookups plus scans of policy catalogs and the source string. It does not contain a general natural-language intent classifier. An exact-source quote can pass this necessary evidence gate and still be semantically unwanted, as SC-08 shows.

## 4. Canonical material digest and allowlisted rendering

**Source:** [`trusted/renderer.py::canonical_bytes`, `render_routine`, `render_escalation`](../src/scoperelay/trusted/renderer.py). **Tests:** `tests/security/test_protected_resource.py`.

Normalize string values to Unicode NFC, serialize stable sorted compact JSON as UTF-8, and hash with SHA-256. Render fixed templates from typed verified facts and closed field IDs; reject uncontrolled fields/values. Digest cost is O(m log m) for key ordering plus O(n) serialization/hash for payload size n; the action material is small and bounded. Digest equality detects change, not authorization: the resource still rechecks current records and approval.

## 5. Protected resource admission and outcome classification

**Source:** [`trusted/resource.py::ProtectedCommunicationResource`](../src/scoperelay/trusted/resource.py), [`storage/transaction.py::immediate`](../src/scoperelay/storage/transaction.py). **Tests:** `tests/security/test_protected_resource.py`, `test_approval_04c.py`, `test_recovery_04c.py`, `test_recovery_process_04d.py`, and `test_writer_lifecycle_04e.py`.

Within the serialized writer transaction, verify caller context, fixture epoch, episode/action/business identity, current policy/grant/assignment/order/contact state, exact stored material, and any independent one-use approval; write the attempt/effect/queue/journal together and commit; otherwise roll back or persist the specific authoritative policy denial/technical outcome according to the path. Duplicate action or business identity is not a new operation. Indeterminate outcomes require same-identity reconciliation and writer quiescence evidence. Database transaction work depends on indexed row count and SQLite contention; this is local single-instance logic, not a distributed algorithm.

Known exception: one coherent-all-state rollback regression remains xfailed. Therefore these tests do not establish that every arbitrary snapshot rollback is detectable; protected effects stay NO-GO.

## 6. Offline evaluator isolation

**Source:** [`evaluation/offline_scenarios.py::evaluate_scenario`, `evaluate_all`](../src/scoperelay/evaluation/offline_scenarios.py). **Tests:** `tests/unit/test_offline_evaluation_08a.py`.

For each known scenario, create a disposable DB, run a fixed scripted transport with no oracle connection, snapshot protected state, then read oracle truth only after runtime evidence is fixed; compare observed decision/proposal with a predeclared judgment and verify no protected-state mutation. For eight cases, runtime is approximately the sum of eight bounded scripted runs and isolated DB operations. Results measure these scripts and assertions, not stochastic model accuracy or security against all attacks.

## 7. Static artifact canonicalization

**Source:** [`public_showcase.py::_canonical_text_bytes`, `_write_json`, `build`](../src/scoperelay/evaluation/public_showcase.py). **Test:** `tests/integration/test_showcase_08b.py::test_static_build_canonicalizes_windows_newlines_and_matches_release_manifest`.

Read UTF-8 source files through universal-newline handling, encode copied static text with LF, hash the source fixture after the same normalization, and serialize compact key-sorted JSON with the frozen artifact's explicit CRLF terminator. The static output inventory is closed at eight filenames; the verifier checks exact SHA-256 values. For a payload of n bytes, canonicalization and hashing are O(n); JSON key sorting is O(k log k) for k keys. The normalized conventions make the output independent of Windows checkout CRLF while preserving the reviewed artifact bytes.
