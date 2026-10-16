# Third-party notice inventory, candidate lock

This is a **local metadata inventory**, not a legal clearance or a vendored-notice file. It is derived from the installed Python 3.13 candidate environment and `uv.lock` (34 resolved packages: ScopeRelay plus 33 third-party distributions). The Git candidate commits the lock and dependency declarations, **not** `.venv`, wheels, the local Ollama model, Playwright, FFmpeg or provider credentials. License labels below come from package `METADATA`; ambiguous classifier-only labels and dual licenses require checking the actual upstream license files before redistribution of binaries.

**Publication-mode review (2026-10-16):** Apache-2.0 for ScopeRelay's own material does not relicense independently installed libraries. The committed `uv.lock` declares dependencies but contains no vendored wheel, package source or model weight. Thus the candidate source repository does not redistribute the 33 third-party dependency distributions, and their binary/source redistribution notices are not triggered by this Git payload alone. The listed MIT/BSD/Apache/PSF/MPL metadata presents no identified conflict for declaring ScopeRelay's *own* code Apache-2.0. This is a distribution-scope inference, not a license audit of every upstream file. If a future release bundles packages, wheels, copied source or a standalone executable, review their actual license/notice files, including [MPL-2.0 source and notice duties](https://www.mozilla.org/en-US/MPL/2.0/) and [Apache-2.0 Section 4](https://www.apache.org/licenses/LICENSE-2.0). The classifier-only `colorama`/`Jinja2` labels, dual-expression `packaging`/`sniffio`/`tqdm`, and upstream Apache `NOTICE` files remain **BLOCKED for bundled redistribution** until verified. The root `NOTICE` contains only the owner-supplied ScopeRelay attribution, not invented third-party credits.

| Distribution | Locked version | Installed license metadata |
| --- | --- | --- |
| `annotated-doc` | `0.0.5` | MIT |
| `annotated-types` | `0.8.0` | MIT |
| `anyio` | `4.15.1` | MIT |
| `certifi` | `2026.7.22` | MPL-2.0 |
| `click` | `8.5.0` | BSD-3-Clause |
| `colorama` | `0.4.6` | OSI Approved :: BSD License |
| `distro` | `1.9.0` | Apache License, Version 2.0 |
| `fastapi` | `0.143.0` | MIT |
| `h11` | `0.16.0` | MIT |
| `httpcore` | `1.0.9` | BSD-3-Clause |
| `httpx` | `0.28.1` | BSD-3-Clause |
| `hypothesis` | `6.169.1` | MPL-2.0 |
| `idna` | `3.20` | BSD-3-Clause |
| `iniconfig` | `2.3.1` | MIT |
| `Jinja2` | `3.1.6` | OSI Approved :: BSD License |
| `jiter` | `0.17.0` | MIT |
| `MarkupSafe` | `3.0.4` | BSD-3-Clause |
| `openai` | `2.54.0` | Apache-2.0 |
| `opentelemetry-api` | `1.45.1` | Apache-2.0 |
| `packaging` | `26.3` | Apache-2.0 OR BSD-2-Clause |
| `pluggy` | `1.6.0` | MIT |
| `pydantic` | `2.14.1` | MIT |
| `pydantic_core` | `2.50.1` | MIT |
| `Pygments` | `2.21.0` | BSD-2-Clause |
| `pytest` | `9.1.1` | MIT |
| `ruff` | `0.17.0` | MIT |
| `sniffio` | `1.3.1` | MIT OR Apache-2.0 |
| `sortedcontainers` | `2.4.0` | Apache 2.0 |
| `starlette` | `1.8.0` | BSD-3-Clause |
| `tqdm` | `4.70.1` | MPL-2.0 AND MIT |
| `typing-inspection` | `0.4.4` | MIT |
| `typing_extensions` | `4.16.0` | PSF-2.0 |
| `uvicorn` | `0.54.0` | BSD-3-Clause |

Direct runtime dependencies are FastAPI, Jinja2, OpenAI SDK, Pydantic and Uvicorn; HTTPX, Hypothesis, pytest and Ruff are development dependencies. Transitives are listed because a packaged distribution or future vendoring could carry their notice obligations. A source-only repo with no vendored dependencies is not a bundled binary. ScopeRelay's project-level license is the root Apache-2.0 `LICENSE` and owner attribution is in `NOTICE`; `pyproject.toml` points package metadata at the root license file, but does not use an SPDX license expression.

Asset/model-output notes: the owner attests to rights to included SVGs, demo frames, captions and historical traces. FFmpeg and Playwright/Edge executables are not included. The [Task 07G report](TASK07G_STRUCTURED_LIVE_VERIFICATION_REPORT.md) identifies the historical generation as local Ollama `gemma4:e4b`, CPU-only, synthetic SC-01, with four model requests; the [trace exporter](../../src/scoperelay/evaluation/trace_manifest.py) emits report-backed checkpoints and explicitly says raw full A/B continuations were not retained. Read-only `ollama show gemma4:e4b --license` returned Apache-2.0 for the installed local model, and [Google's Gemma 4 license](https://ai.google.dev/gemma/apache_2) is Apache-2.0. [Ollama's Gemma 4 E4B listing](https://ollama.com/library/gemma4:e4b) also shows Apache-2.0. No separate prohibition on publishing generated output was identified in these applicable license texts; that conclusion is an inference, not a guarantee about ownership, privacy or all possible terms. No model weights are committed. The older [Gemma Terms](https://ai.google.dev/gemma/terms/) explicitly route Gemma 4 to its separate license, so do not apply that older model-family notice by name alone. A different model, hosted provider, copied raw prompt or bundled weight remains **BLOCKED pending its own terms review**. No external font, photograph, model weight, third-party logo or embedded npm bundle was identified in the tracked candidate; this is not an independent asset-rights audit.
