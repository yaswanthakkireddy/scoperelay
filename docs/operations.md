# Operations and release boundaries

## Supported local operation

The supported reproducible path is Windows, CPython 3.13.x, locked `uv` dependencies, synthetic fixtures, local SQLite, offline tests, and static artifact build/verification. Select DB and sandbox directories outside the source checkout. The reviewer UI binds to loopback in its runner; do not publish it by reverse proxy or expose it to a shared network.

The canonical builder is `uv run --locked python apps/showcase/build.py`; it creates `showcase/public/` from tracked sources, normalizes copied UTF-8 text to LF, and writes compact JSON with the manifest's fixed CRLF terminator. `uv run --locked python scripts/verify_static.py` fails on missing, extra, or mismatched output files. No restored/cached output is an input to the build. The Task 10 source archive and the curated source are separately reproducible against the same unchanged manifest.

## Provider-disabled default and live boundary

Tests and static playback require no provider. CI sets `SCOPERELAY_LOCAL_LIVE_AUTHORIZED=NO` and does not make provider requests. `ProviderAdapter` requires explicit trusted provider/model/base URL/key and bounded request configuration; the live smoke CLI requires an explicit command-line confirmation plus a new output DB. The local sandbox separately requires its explicit enable/live flags, guided synthetic SC-01 and a local request cap. These are code gates, not a substitute for verifying provider entitlement, terms, retention, billing, rate limits or owner authorization. No hosted kill switch or distributed budget service is present.

## Deployment and rollback

No Vercel deployment is part of local operations or GitHub Actions. On 2026-10-16 the read-only Vercel project API/CLI reported `paused=false`, `gitProviderOptions.createDeployments=enabled`, and latest production deployment `dpl_89pgyxx2SWs3rm97XkC68s1eo2Y8` (`READY`, `PROMOTED`). It maps `scoperelay-showcase.vercel.app` and `scoperelay-showcase-yaswankum2622-codes-projects.vercel.app` to that existing deployment. Git repository linkage was absent from the project API/CLI output; Vercel MCP project, deployment and alias reads returned 403. Missing link data is not proof that the project is unlinked. GitHub publication's deployment side effect therefore remains **UNVERIFIED**; do not connect, deploy, resume or alter aliases as part of this guide.

For a future separately authorized static replacement, preserve the current production deployment and both aliases first, verify exact deployment IDs and payload, and document a tested alias rollback before the replacement. This repository does not assert a current rollback result or authorize that operation.

## Failure and recovery

Provider timeout is not proof of provider non-processing. A lost resource acknowledgment is `INDETERMINATE` until same-identity reconciliation. Recovery/writer ownership is local SQLite machinery and is not a hosted recovery service. The coherent rollback xfail remains unresolved; no protected-effect operations should be enabled.

## Release verification

Run the development checks, inspect the curated root-commit lineage, scan the full publication tree for secrets, verify LICENSE/NOTICE and video/source provenance, then build and verify from a clean source checkout. The frozen Task 10 archive independently rebuilds the historical artifact; the current curated source also reproduces all eight unchanged hashes. Local pass does not establish GitHub-hosted CI, Vercel Git integration safety, or a public live service. Vercel linkage remains unverified because authorized Vercel API reads currently return 403; publication/deployment requires separate owner approval.
