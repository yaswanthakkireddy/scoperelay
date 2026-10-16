# Problem statement

## The engineering problem

An AI agent can produce a plausible action proposal; that proposal is not authorization and is not evidence that the action is correct or completed. In a support workflow, the system must establish independently who is assigned, what operation is allowed, which immutable source supports the request, which account/order/contact is authoritative, and whether any effect actually committed. A successful read-tool response can still be stale, out of scope, or unrelated to the customer's actual intent.

ScopeRelay is a local-first, synthetic reference implementation for examining those boundaries in a narrow support workflow. Its current implementation combines two bounded agent roles, deterministic authorization and evidence checks, a SQLite synthetic fixture/resource, read-only reviewer interfaces, an offline scenario evaluator, and an independently built static showcase. It is not a general-purpose agent platform or a production customer-support service.

## Why execution differs from text generation

Text generation returns content. Tool-using execution adds state transitions and authority questions: which tool was called, by which role, with what server-owned scope; whether the actual result returned to the same conversation; what evidence justified the next transition; and whether a protected resource recorded an effect. A model's confidence, a JSON field named `approved`, an HTTP success, or an explanation cannot answer those questions. ScopeRelay treats model output and source text as untrusted inputs and makes trusted services resolve authority and business facts.

## Synthetic example

The synthetic SC-01 ticket T-001 asks for the status of ORD-101. Agent A reads the assigned immutable ticket source and returns a structured interpretation with a quotation. For V2 episodes, trusted code verifies that the exact quotation occurs once in the scoped source/version/digest and derives the source offsets. Authorization independently checks the active manager grant, assignment, policy, order ownership/status, and verified synthetic recipient. Agent B then receives a separate H1/H2 handoff, calls the scoped verified-order read tool, receives its actual observation in the same conversation, and proposes only allowlisted status fields. The observed outcome is an `ALLOW` and a bounded `PROPOSED` status message. In the public sandbox and offline evaluation, that proposal is not submitted to the protected communication resource: effects remain disabled and no delivery is claimed.

That outcome demonstrates bounded proposal generation and evidence handling, not that a real person asked the request, that a message was delivered, or that every authorized proposal is semantically correct. SC-08 deliberately retains a semantically unwanted but structurally authorized proposal as a residual.

## Users and evaluation

The intended readers are engineers, security reviewers, and technical evaluators who want to inspect a concrete synthetic example of policy-separated agent execution. Success means the documented local checks reproduce, provenance stays tied to source and tool observations, unsafe authority claims do not change trusted records, and limitations remain visible. Failure includes forged authority, an out-of-scope read, fabricated continuation/effect evidence, unverified source binding, duplicate or misreported resource outcomes, leaking secrets, or presenting replay as new inference.

## Scope and exclusions

Included: synthetic records only; exactly Agent A and Agent B; two modeled protected-operation classes; five workflow decisions; deterministic offline scenarios; historical Task 07G evidence; local reviewer/sandbox apps; a static eight-file showcase.

Not established or enabled: public hosted inference, external communications, real customer data, payment/account changes, an operational public playground, protected-effect release, universal prompt-injection resistance, independent certification, production availability, or proof of customer intent. The coherent all-state rollback case remains an expected-failure test and blocks protected-effect release.
