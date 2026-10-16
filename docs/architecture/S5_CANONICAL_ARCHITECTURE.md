# Project 1 — Final Architecture Specification

**Secure Bounded Action Extraction and Multi-Agent Handoffs**  
**Status:** Conceptual architecture consolidated; implementation and empirical validation not authorized.  
**Purpose:** Bounded Applied AI security portfolio demonstration—not a new authorization, provenance, or commercial security product.

## 1. Final problem statement

An authenticated support manager delegates routine ticket handling to autonomous agents. A customer ticket may legitimately request work, but its text is lower-trust data. When Agent A extracts or paraphrases a request and sends it to Agent B, source-derived text may be mistaken for a new trusted instruction. **The failure occurs when a proposal derived from a ticket is promoted into executable authority without an independent grant.** Authentication of either agent, or of the ticket submitter, does not by itself authorize the requested action.

## 2. Exact project objective

Demonstrate that **two LLM agents can discover and carry out legitimate, pre-authorized customer-support requests from untrusted tickets without allowing those tickets to expand authority**. Preserve useful routine automation; independently verify the material operation before any synthetic effect; require trusted approval for predefined exceptions; withhold action when essential intent, authority, or evidence is unclear. Separate unsafe proposals, enforcement denials, committed effects, and legitimate task completion in future evaluation.

## 3. Bounded scope

**Supported:** One synthetic support organization and assigned customer tickets; Agent A's request extraction; Agent B's communication preparation; automatic routine order-status replies; verified customer/account/order/recipient resolution; restricted customer-visible data fields; malicious or ambiguous ticket content; clarification; and independently approved, sanitized **internal support escalation** as the sole exceptional operation class. All outbound effects are records in a synthetic message queue.

**Unsupported:** Real emails or customer data; payments; production account changes; external exports of full account history; arbitrary file attachments; free-form disclosure of confidential notes; unrestricted operation creation; general-purpose agent networks, gateways, provenance engines, or policy languages; and verification of downstream business-process terminal outcomes.

**Scoped later decision (ADR-020, approved 2026-10-15):** A separate synthetic-only, no-effect public sandbox may demonstrate the two approved read-tool agents. It has no protected writer, API-108/109, supervisor mutation, queue, recovery control or external customer delivery. Its visitor sessions and evidence are not S5 authority. This does not alter this architecture's protected operation classes, decisions, outcomes, ADR-006/019 or recovery conditions. Public deployment and hosted inference retain separate gates.

## 4. Logical architecture and responsibilities

```text
Authenticated manager + organization policy
                  |
                  v
         Trusted task authority  <--- Verified approval authority
                  |                   (predefined exception only)
Untrusted ticket  |                                  ^
       |          v                                  |
       +----> Agent A: interpret and propose ----> Trusted action resolution
                       |                           ^  |  ^
                       |   Verified customer, account, order, recipient
                       |                           |  |
                       +-- candidate request ------+  |
                                                      v
       Trusted handoff: grant | verified facts | untrusted data | sources
                                                      |
                                                      v
                                          Agent B: bounded communicator
                                                      |
                                                      v
                                Authoritative synthetic communication service
                                 (recheck grant, recipient, content, state)
                                                      |
                                                      v
                                Accepted / denied / indeterminate effect record
```

Only **Agent A and Agent B** are LLM agents. The task authority, approval authority, account/order records, and communication service are independently trusted application responsibilities—not additional agents. The communication service is the final enforcement point for every protected synthetic send.

## 5. Trust boundaries and action-authority contract

| Information or authority | Trusted owner | Agent-supplied content cannot do |
|---|---|---|
| Manager's initiating task | Authenticated manager; trusted workflow record | Impersonate or amend the initiating instruction |
| Permitted operation and disclosure policy | Organization-controlled policy | Create an operation class or override confidentiality restrictions |
| Customer, tenant, order, recipient, assignment | Authoritative account and ticket services | Assert ownership or substitute an unverified destination |
| Extracted request, quoted text, paraphrase | Ticket and Agent A (lower trust) | Grant permission or become a system instruction |
| Additional exceptional approval | Authenticated authorized approver; trusted record | Treat a model summary or claimed approval as the approval itself |

**Trusted grant fields:** task identity; initiating principal; delegated actor; operation class; tenant and resource scope; approved recipient rule; allowable disclosure fields; validity/expiration and revocation; required approval and its exact scope; current resource-state preconditions; replay/use constraints; and binding to the **material proposed operation**.

**Candidate fields from Agent A:** ticket/source references; proposed action class; inferred customer/order identifiers; requested recipient/content; extracted evidence; conflicting interpretations; and uncertainty. These are *proposals*, never trusted grant fields. Structured formatting alone adds no authority.

For an operation to be accepted, the resource must independently verify identity, active grant, operation class, resource ownership, recipient, disclosure fields, applicable approval, current state and replay safety. Missing or contradictory material fields prevent execution. A downstream agent cannot enlarge the task by editing a handoff. Established authentication and resource authorization suffice; no new token or cryptographic scheme is proposed.

## 6. Agent responsibilities and outputs

**Agent A — Request interpreter.** Input: assigned ticket and permitted supporting data, plus a trusted instruction to *extract possible work*. It reads, summarizes, classifies and proposes routine actions or escalation candidates. Output: a structured candidate action, source references, extracted parameters, uncertainty and conflicts. It **cannot** authenticate customer ownership, verify recipients, grant authority, approve exceptions, or instruct B to exceed the trusted task.

**Agent B — Bounded communicator.** Input: an **independently resolved trusted operation instruction**, verified parameters, lower-trust ticket material clearly separated from instructions, and source references. It may prepare a status reply from approved customer-visible fields or an approved, sanitized internal escalation. Output: a proposed synthetic communication or a clarification/escalation indication. It **cannot** accept Agent A's wording as proof of authorization, choose a ticket-supplied external recipient, or override final resource checks.

A handoff has four distinct compartments: **(1) trusted task/grant reference, (2) verified parameters, (3) source-derived candidate information, (4) independently resolvable source references**. Paraphrases, combined summaries and conflicting ticket excerpts remain lower-trust. Model-generated provenance labels are not themselves evidence of origin. The design needs a **narrow source-aware handoff**, not a universal semantic-provenance engine.

## 7. Action decision semantics

| Decision | Determining condition | Effect |
|---|---|---|
| **ALLOW** | Verified candidate falls within a valid grant, matches current resources and allowed disclosures, and has any required approval | B may propose the bounded operation; resource rechecks it |
| **DENY** | Known policy prohibition, incompatible tenant/resource/recipient, or forbidden disclosure | No protected effect |
| **CLARIFY** | A genuine request or required parameter is ambiguous or conflicting | Ask the appropriate authenticated requester; no speculative protected effect |
| **REQUEST APPROVAL** | Proposed action is in the **predefined** exceptional internal-escalation class but lacks its required grant | Suspend until independent exact-action approval or rejection |
| **INCOMPLETE EVIDENCE** | Required trusted identity, ownership, policy, approval or resource-status evidence is unavailable | Withhold operation; do not equate an outage with a policy denial |

Agent classification is advisory. The decision and final resource enforcement use independently trusted facts. Approval can activate only an organization-defined exceptional class, bounded to material operation, ticket, internal recipient and permitted data fields. A model confidence score never authorizes execution. When the exact request is semantically uncertain, deterministic scope checks cannot establish the user's true intent.

## 8. Protected security invariants

1. **Authority non-expansion:** Ticket text, Agent A's interpretation and Agent B's plan cannot issue or enlarge a grant.
2. **Handoff separation:** Source-derived requests are never controlling instructions merely because an authenticated agent relayed or paraphrased them.
3. **Resource-scoped enforcement:** Every accepted synthetic send satisfies current task, actor, tenant, ticket, order and recipient constraints at the authoritative communication service.
4. **Bounded confidentiality:** A routine customer reply contains only verified **customer-visible** order-status fields and controlled response text—not arbitrary model-composed content, internal notes, account history or attachments.
5. **Exact exceptional approval:** A trusted approval covers only its recorded material internal-escalation action and cannot authorize a changed target, recipient or disclosure.
6. **Freshness and replay safety:** Expiry, revocation, assignment/ownership changes, duplicate actions and consumed approvals are evaluated before acceptance.
7. **Fail closed on missing authority:** Absence of trusted evidence cannot become ALLOW; a lost response cannot justify an uncorrelated retry.
8. **Honest outcomes:** Proposed forbidden actions, explicitly policy-blocked actions, accepted effects and indeterminate outcomes are different categories.

All are **conditional design invariants**: they hold only if trusted components enforce their stated responsibilities and all relevant protected routes are covered. They are not experimentally verified guarantees.

## 9. State-transition model

```text
DISCOVERED → CANDIDATE_EXTRACTED → TRUSTED_PARAMETERS_RESOLVED
                                      |
                                      v
                              AUTHORITY_EVALUATED
                               /   |     |       \
                             DENY  CLARIFY APPROVAL_PENDING INCOMPLETE_EVIDENCE
                                       |       |
                           (verified new facts / exact approval)
                                       v
                              AUTHORITY_EVALUATED
                                       |
                                     ALLOW
                                       v
                           AUTHORIZED_FOR_DELEGATION
                                       v
                                DELEGATED_TO_B
                                       v
                              RESOURCE_EVALUATION
                               /       |         \
                           REJECTED  ACCEPTED   INDETERMINATE
                                       |
                                   COMPLETED
```

**Prohibited transitions:** ticket text directly to ALLOW; Agent A's declaration to approval; unresolved conflict to send; missing authoritative evidence to send; and indeterminate send to a new uncorrelated attempt. Before accepting, the resource rechecks active grant, approval, recipient, ownership and applicable use constraints. Duplicate ticket processing and retries reuse a stable action identity. A lost acknowledgment requires reconciliation with authoritative effect records before any further attempt.

## 10. Eight representative design scenarios

*Hypothetical design traces—not executed attack results.*

| # | Trusted task and lower-trust input | Agent A proposal and authority decision | B/resource behavior and final state | Residual risk |
|---|---|---|---|---|
| **1. Routine status** | Authorized status replies; verified customer asks about owned order | A proposes status reply; verified account/order/recipient → **ALLOW** | B composes approved fields; resource accepts once → **COMPLETED** | Possible wording or interpretation error |
| **2. External history export** | Routine task; ticket demands full account history sent externally | A may propose export; outside operation/data/recipient scope → **DENY** | No executable export; resource rejects attempted send → **DENIED** | Unsafe proposal may still occur |
| **3. Confidential content to correct recipient** | Verified customer asks for status plus internal notes | A proposes both; status may be allowed but notes forbidden → **DENY** for disclosure | B may separately send sanitized status; forbidden message rejected | Semantic leakage if unconstrained text is allowed |
| **4. System-message spoofing** | Ticket includes fake system/developer instruction | A may quote or repeat it; no trusted instruction change → **DENY** expansion | B receives only original task grant; resource rejects forbidden effect | Model may still be influenced at proposal stage |
| **5. Paraphrased injection** | Ticket's malicious export request becomes a neutral-sounding “compliance follow-up” | A proposes expanded task; paraphrase retains lower-trust origin → **DENY** | B cannot promote it; resource rejects external disclosure | Boundary fails if paraphrase is copied into trusted instruction channel |
| **6. Approved exception** | Genuine request for internal senior-support escalation | A proposes defined exception → **REQUEST APPROVAL**; authorized supervisor approves exact internal recipient/fields | B sends sanitized escalation; resource accepts → **COMPLETED** | Approver may misunderstand despite material-action preview |
| **7. Conflicting requests** | Two messages identify different plausible orders | A surfaces conflict; target uncertain → **CLARIFY** | B may send a separately permitted nonsensitive clarification; no status disclosure → **CLARIFICATION_PENDING** | Legitimate response delayed |
| **8. Permitted but unwanted reply** | Quoted text resembles a genuine request but customer did not intend action | A misclassifies as routine; all enforceable fields may still pass → possible **ALLOW** | B/resource may accept bounded reply → **COMPLETED** | **Semantically unwanted but technically authorized action remains possible** |

## 11. Known limitations and non-guarantees

This design **does not guarantee** correct interpretation of arbitrary customer intent, absence of all unwanted but permitted routine actions, universal prompt-injection immunity, complete semantic or implicit-flow provenance, correct handling of undisclosed alternate routes, or confidentiality of arbitrary unrestricted model-generated prose. Authenticated customers and authorized approvers may act maliciously or make mistakes. Policy, identity, account records or source references may be incomplete or wrong. A successful resource authorization does not guarantee a broader business-process terminal outcome.

The routine-reply guarantee is intentionally narrower: the permitted response draws from a specified set of independently verified customer-visible fields. Any claim of general free-form content safety would require stronger controls and a separate evaluation.

## 12. Future empirical evaluation acceptance criteria (not executed)

A later evaluation must use **actual two-agent model behavior**, benign and attacker-controlled synthetic tickets, and the same tasks, permissions and resource evidence across a correctly configured conventional baseline and this bounded workflow. It must record complete handoffs, proposals, explicit policy denials, authoritative committed effects and indeterminate outcomes. Independent synthetic resource records—not model judgments—determine security outcomes.

**Acceptance conditions:**

- Every executed synthetic disclosure in the tested scope respects tenant, verified recipient, permitted fields, active grant and approval requirements; any prohibited commit fails the security gate.
- Malicious source text cannot obtain additional authority by being quoted, paraphrased or placed in a structured proposal in tested handoffs.
- Legitimate routine status replies and genuinely approved exceptions can complete; unnecessary denials, clarifications and approval requests are separately measured.
- Report rates and denominators separately for **forbidden proposals**, **verified policy-blocked attempts**, **prohibited committed effects**, **legitimate completions** and **semantically unwanted yet permitted actions**.
- Repeat adversarial and legitimate cases, disclose stochastic variation, incomplete traces, unknown outcomes, latency and inference cost; do not treat a finite passing suite as universal proof.
- A strong conventional orchestration/multi-CaMeL-style baseline receives equal trust assumptions, authority, cases and evidence. Equivalent results are reported as equivalence, not invented superiority.

These are **future acceptance criteria**, not benchmark results, formal verification or authorization to implement.

## 13. Honest portfolio positioning

This is a **bounded Applied AI security engineering portfolio project**: two autonomous agents, legitimate request extraction from adversarial source material, scoped delegation, trust-preserving handoffs, selective exceptional approval, and deterministic effect-level checks. Its distinct engineering challenge is preserving legitimate utility while preventing source content from acquiring authority.

**CaMeL and multi-CaMeL already establish central control/data separation and multi-agent provenance principles**, and conventional secure orchestration already supplies authentication, fine-grained authorization, exact approvals and resource-side enforcement. This specification claims **no novel security primitive, generalized provenance guarantee, commercial superiority or validated demand**. It is distinct from Indic voice-agent compliance (VaaniRecover Lab) and business-process terminal-outcome verification (OutcomeProof).

**ARCHITECTURE CONSOLIDATED — DESIGN STAGE COMPLETE. IMPLEMENTATION NOT AUTHORIZED.**
