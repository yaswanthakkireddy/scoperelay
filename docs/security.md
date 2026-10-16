# Security model and limitations

ScopeRelay is a synthetic engineering reference. This document describes implemented controls and explicit gaps; it is not a professional security audit, certification, or guarantee of prompt-injection immunity. Report vulnerabilities using the owner-authorized instructions in [SECURITY.md](../SECURITY.md).

## Assets and attacker assumptions

Protected assets include synthetic grants/policy/identities, source-version integrity, assignment and order/contact scope, supervisor approval/material, the synthetic queue and journal, and secrets used by any explicitly configured local provider. Attacker-controlled inputs include customer/ticket text, model proposals, tool arguments, malformed provider responses, browser inputs and stale/replayed identifiers. The local administrator and an attacker with full write access to the machine/database are not isolated by Python module boundaries.

## Enforced boundaries

- **Authority:** `trusted/authorization.py` resolves grants, policy, assignment, ownership, record status and recipients from SQLite. Agent/customer text cannot mint a grant or supervisor principal.
- **Tools:** `trusted/tool_dispatcher.py` admits only A's ticket read and B's verified-order read with exact server-bound IDs and argument shapes.
- **Source grounding:** exact source/version/digest/tool-event correlation is validated before candidate evidence is accepted. Exact quotation supports location, not human intent.
- **Cross-agent handoff:** trusted H1/H2 records are distinct from lower-trust A content/source references; B cannot change them or add arbitrary material.
- **Output:** fixed renderer and allowlisted verified fields constrain material. An accepted proposal still is not a committed effect.
- **Protected resource:** only the trusted resource module admits synthetic effects; the public static showcase does not import or call it. Independent escalation approval and current-state admission exist in local code, but protected release remains blocked by the known xfail.
- **Browser local boundary:** reviewer UI is GET/HEAD-only and loopback/host/origin constrained; sandbox POST requests require local origin, bounded input and security headers. These controls do not establish hosted authentication or safe reverse-proxy behavior.
- **Local quotas:** sandbox code bounds active sessions (20), sessions/day (100), runs/session (5), offline turns/day (40), and session lifetime (30 minutes). These are single-instance local counters, not distributed rate limits or a public abuse-control guarantee.
- **Secrets and CI:** no keys belong in fixtures/traces/screenshots. CI uses read-only repository permission, runs offline with live authorization set to `NO`, and has no deploy workflow.

## Known gaps and nonclaims

- One coherent-all-state rollback test is xfailed; arbitrary rollback and restore tampering are not proven detectable.
- Local SQLite/process writer ownership is not distributed locking or multi-instance isolation.
- Sandbox quota logic is local/single-instance. Distributed quotas, public abuse prevention, provider retention/terms, hosted rate limits and cost isolation are not established.
- No public live inference endpoint is approved. Provider live execution is explicitly gated and no CI credentials are supplied.
- Historical evidence is report-backed, not signed raw transcript. One SC-01 run does not show universal model behavior.
- Conservative relevance heuristics do not infer actual customer intent; SC-08 records a semantic residual.
- The local supervisor service is not an independently authenticated production approval workflow. The security UI's session/CSRF lifecycle is not promoted as a production feature.
- No professional penetration test, compliance review, external delivery, or security certification is claimed.

## Security tests

Relevant coverage is in `tests/security/`, `tests/contracts/` and `tests/integration/test_read_only_agents_05a.py`: forged scope, forbidden fields, wrong recipient/owner, source mutation/repetition, handoff changes, stale approval/grant, replay, concurrency, lost acknowledgment and recovery conditions. The exact collected suite and known xfail are in [evaluation](evaluation.md). Tests establish only the cases they execute.
