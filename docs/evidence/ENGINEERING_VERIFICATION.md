# Engineering verification record

Local verification of ScopeRelay on Windows, CPython 3.13.16, `uv` 0.13.1, and the locked dependency environment. Current hosted status is shown under GitHub Actions. No deployment, new provider inference, or protected effect is claimed by these checks.

## Code and environment checks

| Check | Command | Observed result |
|---|---|---|
| Full offline Python suite | `uv run --locked pytest -o addopts=''` | **420 passed, 1 xfailed, 1 warning; 421 collected.** Xfail is the known coherent-all-state rollback limitation; warning is Starlette/httpx TestClient deprecation. |
| Isolated showcase fixture regression | `uv run --locked pytest tests/integration/test_showcase_08b.py -q` | 9 passed. Tests build offline exports under `tmp_path`; a distinct test verifies that a missing export fails closed. |
| Syntax | `uv run --locked python -m compileall -q src tests apps/showcase scripts` | Pass |
| Ruff lint | `uv run --locked ruff check src tests apps/showcase scripts` | Pass |
| Ruff format | `uv run --locked ruff format --check src tests apps/showcase scripts` | Pass; 87 files formatted |
| Lock | `uv lock --check` | Pass; 34 packages, Python `>=3.13,<3.14` |
| Dependency audit | `uvx --from pip-audit==2.10.1 pip-audit --path .venv/Lib/site-packages` | No known findings in installed third-party packages; local `scoperelay` is not on PyPI and is unaudited by this command |
| Documentation links | `uv run --locked python scripts/check_markdown_links.py` | `MARKDOWN_LINKS_PASS` after reconciliation docs |
| Package build | `uv build --out-dir <fresh-temp>/dist` | Source distribution and universal wheel built successfully from the clean curated source export |

## Static artifact checks

| Check | Command | Observed result |
|---|---|---|
| Current-checkout build | `uv run --locked python apps/showcase/build.py`; `uv run --locked python scripts/verify_static.py` | **8/8 exact** after newline canonicalization; manifest unchanged |
| Frozen source reproduction (F → R) | Fresh extraction of the 79-file archive; `uv run --offline --locked --python <Python 3.13.16> python showcase/build.py`; compare eight outputs to manifest | **8/8 exact**; installed 33 locked packages using the existing Task 13 uv cache |
| Curated source reproduction (C → R) | Fresh `git archive` of commit `44005b45a19f785418536b3b6f75e01aa15e546d`, clean extraction; `uv run --locked --python <Python 3.13.16> python apps/showcase/build.py`; run `scripts/verify_static.py` | **8/8 exact**; installed 34 locked packages; no existing generated output was extracted |
| Frozen artifact identity (R) | Independently extracted Task 10 output; compare exact filenames and hashes | Exactly 8 files; all 8 match the frozen manifest |

The static HTML/CSS/JavaScript outputs are byte-for-byte the same as the previously reviewed eight-file payload. The final clean export was run through `uv run --locked --with playwright==1.57.0 --python <Python 3.13.16> python tests/browser_qa/browser_qa_interactive_harness.py`: `LOCAL_EDGE_PASS`, Edge 155.0.4283.45, five viewports (1440/1024/768/390/320) with no overflow, six screenshots, successful demo/deep-link/case navigation, keyboard Enter/Tab behavior, missing-trace fail-closed state, no console/bad-response/off-origin requests, HTTP 200 read of the currently deployed alias, zero model generations and zero protected requests. Video recording was unavailable because ffmpeg is not installed. QA screenshots/video were written only under the temporary clean export, not this repository. The offline showcase integration tests also pass, covering routes, scripts, content security policy and live/effect isolation.

The source-to-artifact comparison was three-way: **F** is `source.zip` (SHA-256 `36b406e441e10bf73b4bf49fa17213ff1ee642d56fb56299292a3f31b1400289`), **R** is the reviewed eight-file output, and **C** is a clean export of the curated Git source. Manifest raw-byte SHA-256 is `940ddf66d9e536ed5257057f7c41cb45c4f29caff972309a00ee3efb51a3d04e`; its Git SHA-1 blob ID is `275db7dedac4d6adb848bf25c86a182583bd6043`. The blob ID is not a competing SHA-256 fingerprint: the manifest has the same Git blob in commits `9dcbb6e`, `8b4b935`, and `HEAD`.

| Output | R = F = C after fix (SHA-256) | C before fix (SHA-256) | Input(s) and confirmed cause |
|---|---|---|---|
| `app.css` | `1a44a1ae872ad4eea39fe0993e9b1126cc3f8dbb6f1e65d1151ad849503ec20d` | `03f892770db80786784a84ab0ed6e7c9f25bf232c89c65b4083f460185275fb5` | F `showcase/src/app.css`; C `apps/showcase/src/app.css`. Contents equal after LF normalization; C checkout had 17 CRLFs, F source 17 LFs. |
| `app.js` | `b0776be2589584b02a3d6a2719c700e2b9d3e2529049d87537d7e53f28116bf5` | `9ff52e87acf7c3d9f03ec9754c6e487e7588f576a728bb5222b960e7fb37a9ef` | F/C `app.js` source pair; content equal after LF normalization; 188 CRLFs versus 188 LFs. |
| `data.json` | `081a1515b91b331d43e2679a83b2dd5cdb39d55273f5d4d08a8d4efaa595917c` | `4a60a737554b11947a8c108aa62b6ea681b35508ec8a6eaedc8b041d42ad1123` | `public_showcase.py` plus `fixtures/scenarios/source_texts.json`. All fields/cases were equal except `fixture_sha256`, which previously hashed raw CRLF-versus-LF fixture bytes. |
| `demo.js` | `b9c95d27068c4971ca9dbdbac09e2b23cff8804f65a4344a8b9476c0c9caaae1` | `9e282af7e2b9702db1ccc337f124d5ecfd0b3cd2b69b8f459f0ff7e4e4c28996` | F/C `demo.js` source pair; content equal after LF normalization; 131 CRLFs versus 131 LFs. |
| `favicon.svg` | `a8c6e789f3b34b7112fc4f358e30c1da592f0bf2da43f16ea07fad2e5ae9503e` | `6b71cd0e036ced080b51e2cfddc1582e755006582738ebca14c42ad0cc59ebc3` | F/C favicon source pair; same text plus checkout newline difference. |
| `index.html` | `dd6e8604a9c8a467a1fe080a89bb1c115711192a6a93983700931a4cc0ac8fc4` | `9c5e1dd1019df90d364513c8351405fa3eaf5b920cf147ec701437dc22dec60e` | F/C `index.html` source pair; content equal after LF normalization; 97 CRLFs versus 97 LFs. |
| `trace.json` | `83fa22a9a649bc88482eecffa0ab3e6cac037d99cee72902b81cf93e005455c4` | `83fa22a9a649bc88482eecffa0ab3e6cac037d99cee72902b81cf93e005455c4` | `trace_manifest.build_manifest()` and its report/source evidence. It matched throughout; no change required. |
| `vercel.json` | `2bb9052dae809f862e62441804cb13eeaabc096639352226b7d64e63602a1cb0` | `dcfe0dde1bfa74310216f50960e3456233eb52ceacbd102b9f4b20a37750893c` | F/C `vercel.json` source pair; content equal after LF normalization; 13 CRLFs versus 13 LFs. |

Confirmed causes: six copied UTF-8 assets were semantically identical; Windows `core.autocrlf=true` made their working copies CRLF while the frozen archive had LF. `data.json` embedded a digest of the raw fixture file, making that metadata platform-dependent. `trace.json` was already deterministic. No missing source, generated ordering, timestamp, model call, semantic UI change, or dependency/runtime divergence caused the prior mismatches. The fix canonicalizes copied text and fixture digest input to LF, and pins generated JSON's CRLF terminator to match the reviewed artifact. The frozen manifest was not edited.

## Browser and scenario evidence

- Local Edge 155.0.4283.45 QA: `uv run --locked --with playwright==1.57.0 python tests/browser_qa/browser_qa_10a.py`; five viewports (1440, 1024, 768, 390, 320), six screenshots, no horizontal overflow, zero generations and zero protected effects. Local QA only; not production or formal accessibility QA.
- `uv run --locked python -m scoperelay.evaluation.offline_scenarios`: eight deterministic scripts; zero model generations, resource attempts, queue effects and journal events. SC-08 remains a semantic residual proposal.
- Historical Task 07G: four previously authorized local Ollama generations for synthetic SC-01; report-backed evidence of separate A/B tool-feedback loops and zero protected effects. This verification made no new inference.
- Owner-approved historical video: 30.44 seconds, MP4 size 1,961,825 bytes; SHA-256 `52afe25674aa4bbbd8e79d0870bfd6689bceef401b5544356043a689e727b98f`. Owner playback approval is not an independent media/legal audit.

## Local executable demo recheck

On 2026-10-16, the current Python 3.13.16 checkout was exercised again without provider inference:

- `uv run --locked pytest tests/integration/test_sandbox_10a.py tests/integration/test_sandbox_10b.py tests/integration/test_sandbox_10c.py tests/integration/test_sandbox_10d.py tests/integration/test_workbench_06a.py -o addopts='' -q`: **57 passed, 1 warning** (the existing Starlette/httpx deprecation).
- `uv run --locked python -m scoperelay.evaluation.offline_scenarios`: eight scenarios; SC-01 bounded proposal, SC-02–SC-05 boundary rejected, SC-06 preapproval only, SC-07 clarify unsent, SC-08 semantic residual proposal. Model generations, protected attempts, and accepted effects were all zero.
- Started the actual sandbox CLI on `127.0.0.1:8770` with `--enable-local-sandbox` and a fresh disposable data directory outside the repository. No live flag was supplied.
- An automated Edge browser clicked Guided demo → Run offline scenario and observed `SC-01`, `COMPLETE`, `ALLOW`, `PROPOSED`, two scoped-tool timeline entries, zero model requests, and protected effect disabled.
- The same browser selected Challenge lab → SC-03 and observed `DENY` with zero model requests. Selecting Local live SC-01 displayed `LIVE UNAVAILABLE`; no live run was submitted. The browser observed no off-loopback requests.
- [The README sandbox screenshot](../../assets/readme/local-sandbox.png) was captured from that actual new offline SC-01 execution. It is neither historical model evidence nor a fabricated UI mockup. The artwork in `assets/readme/*.svg` is explanatory design, not additional execution evidence.

Reproduce the local interaction using [the README walkthrough](../../README.md#try-the-working-local-sandbox). This verifies executable local code with scripted turns; it does not establish fresh genuine LLM success, public hosting readiness, or protected-action release readiness.

## Not established

The clean-checkout CI regression was caused by two integration tests depending on ignored generated `showcase/public/data.json`. The repaired tests build exports in `tmp_path` and retain explicit missing-export fail-closed coverage. Before repository recreation, hosted Offline checks run `38894892068` on `111e79b` passed every configured step; the original CI metadata is archived privately. Dependency review stopped before analysis because that repository's dependency graph was disabled. Current workflow results must be read from the current repository's Actions page. No type checker is configured. Vercel disconnection relies on the owner's Settings → Git evidence; it has not been independently re-read here. No independent security audit, signed raw-trace proof, public live-inference entitlement, protected-effect release, or universal intent/injection correctness is claimed. The known coherent-rollback xfail remains visible and blocks protected-effect release.
