# Cost and abuse controls

The implemented optional LIVE path is **local Ollama only** at `127.0.0.1:11434/v1`, fixed `gemma4:e4b`. No Groq, NVIDIA or hosted provider is connected to the sandbox. The model may consume local CPU/electricity; no claim of literally zero total cost is made. No provider generation ran in this directive.

## Enforced local ceilings

- OFF by default; operator-only CLI flag plus separate local authorization environment marker.
- Visitor must explicitly select Local live SC-01; normal page load, offline case selection and historical replay cannot trigger inference.
- Four turn slots atomically reserved before starting a run; maximum 40 reserved slots per UTC day, two active runs, five runs/session, client/session rate windows and 30-minute session expiry inherited from the local sandbox.
- `ProviderAdapter(max_requests=4, max_retries=0)` counts requests before dispatch. A failed request consumes a slot. Idempotent same-key replay returns the prior run; different body conflicts.
- Persistent operator kill switch prevents new runs. Restart marks unfinished runs interrupted without refund; their exact dispatched request count is uncertain if the process died before terminal bookkeeping.

These limits are **not** a safe public spend authorization. No global shared store, multi-instance lease, provider account entitlement, Free-plan billing state, price ceiling, signed visitor identity, bot defense, proxy-IP trust, hard cleanup SLA or paid-budget approval is established. Current local SQLite state cannot be moved to ephemeral/serverless hosting by changing a URL. Public LIVE remains disabled; see the separate [local-to-public engineering backlog](LOCAL_LIVE_ENGINEERING_BACKLOG.md). The reviewed static page has no inference endpoint or provider credential.
