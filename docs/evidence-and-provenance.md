# Evidence and provenance

## Three modes that must stay separate

| Mode | What it is | What it is not |
|---|---|---|
| `RECORDED_REPLAY` | A static presentation of a prior Task 07G local Ollama SC-01 run. The reviewed trace/report and associated screenshot/video are labeled as historical. | A fresh model call, independently signed raw transcript, or public service. |
| `DETERMINISTIC_COMPONENT_TEST` | Eight fixed synthetic scripts execute the trusted/local logic and compare observed state against evaluator-only labels after runtime. | Stochastic model behavior, red-team accuracy, or general defense rate. |
| `LIVE` | An explicitly authorized `ProviderAdapter` invocation against configured endpoint/model; application evidence records request/call/observation/continuation. | Automatically permitted by the code, covered by CI, or a hosted public capability. |

## Historical Task 07G run

The report [`TASK07G_STRUCTURED_LIVE_VERIFICATION_REPORT.md`](reports/TASK07G_STRUCTURED_LIVE_VERIFICATION_REPORT.md) records a bounded local Ollama `gemma4:e4b` CPU-only SC-01 A→B run: four generations (two A, two B), real scoped read calls and same-conversation continuations, source quotation binding, trusted H1–H4, and a bounded B proposal. The report records zero protected effects and no external delivery. It identifies the model digest, endpoint constraints, call/event correlations, usage metadata, source hash and derived `[0,72)` span.

The report is an owner-authorized historical execution report. Full raw continuation contents were not retained; content hashes/lengths and structured DB evidence support only the claims explicitly stated there. No signed raw-trace attestation is claimed. One successful SC-01 run does not show repeatability, universal schema enforcement, customer intent, or protected-effect safety.

## Sources and artifact integrity

Synthetic source and oracle fixture checksums are in [`FIXTURE_MANIFEST.json`](evidence/FIXTURE_MANIFEST.json). The Task 07G report binds the SC-01 source body hash to the quote. The reviewed static output hashes are in [`STATIC_SHOWCASE_RELEASE_MANIFEST.json`](delivery/STATIC_SHOWCASE_RELEASE_MANIFEST.json); the verifier checks all eight named payload files. The static artifact has a separate identity from this Git source commit.

The screenshot and 30.44-second video are owner-approved publication media. The video SHA-256 and size are recorded in [engineering verification](evidence/ENGINEERING_VERIFICATION.md). Owner playback approval is not an independent legal/media audit.

### Source-to-artifact reconciliation

The frozen Task 10 archive is `release-checkpoints/task10-static-20261015/source.zip` (79 files; SHA-256 `36b406e441e10bf73b4bf49fa17213ff1ee642d56fb56299292a3f31b1400289`). The reviewed artifact (R) is the exact eight-file output described by the unchanged manifest (raw manifest SHA-256 `940ddf66d9e536ed5257057f7c41cb45c4f29caff972309a00ee3efb51a3d04e`). A fresh Python 3.13.16 build from frozen source (F) and a fresh clean export of the curated source (C) both match R 8/8. The initial seven-file discrepancy was Windows CRLF checkout bytes in six copied assets plus the raw-byte fixture fingerprint in `data.json`; all content became byte-identical to the frozen LF source after normalization. No visual/behavioral content, scenario output, trace or manifest digest was changed.

## Evidence limits

There is no claim of signed trace provenance, raw full transcript availability, independent model evaluation, general security proof, live-provider verification in CI, or human signoff beyond owner attestation. Replay, offline results and provider executions must not be combined into a single “model accuracy” metric.
