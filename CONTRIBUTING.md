# Contributing to ScopeRelay

ScopeRelay is an early engineering reference, not a production service. Keep changes synthetic-only, local-first, typed, and traceable to the [canonical S5 architecture](docs/architecture/S5_CANONICAL_ARCHITECTURE.md) and [security policy](docs/security/AUTHORIZATION_POLICY.md).

Install the supported Python 3.13 environment with `uv sync --locked --python 3.13 --no-python-downloads`; run deterministic tests with `uv run --locked pytest -q`. Do not use real customer data, send real messages, add uncontrolled model tools, or invoke live inference in default tests.

Security-sensitive changes require review against the [canonical S5 architecture](docs/architecture/S5_CANONICAL_ARCHITECTURE.md), implementation, policy, data, API, recovery, and test contracts. Do not weaken assertions to make tests pass. Report exact commands and results, and record blockers rather than fabricating evidence. See the root `LICENSE`/`NOTICE` and `SECURITY.md`; GitHub private vulnerability reporting is a separate post-publication configuration action.
