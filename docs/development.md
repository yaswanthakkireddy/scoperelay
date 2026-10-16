# Local development

## Supported environment

- Windows is the verified development platform for this candidate.
- CPython `>=3.13,<3.14` and `uv` 0.13.1 are pinned/documented.
- `uv.lock` is the dependency lock. Offline tests and showcase do not require provider credentials.

## Fresh checkout

```powershell
git clone https://github.com/yaswanthakkireddy/scoperelay.git
Set-Location scoperelay
uv sync --locked --python 3.13 --no-python-downloads
uv run --locked pytest -o addopts=''
uv run --locked ruff check src tests apps/showcase scripts
uv run --locked ruff format --check src tests apps/showcase scripts
uv run --locked python -m compileall -q src tests apps/showcase scripts
uv run --locked python scripts/check_markdown_links.py
uv run --locked python apps/showcase/build.py
uv run --locked python scripts/verify_static.py
```

Clone the public repository URL above, then run these commands from its root. The build commands produce the allowlisted `showcase/public/` output and verify all eight output bytes against the frozen manifest. The builder canonicalizes copied UTF-8 text to LF and uses a fixed CRLF JSON terminator, so Windows `core.autocrlf` settings do not change the artifact. The curated Git source was independently built on Python 3.13.16 and passed 8/8.

## Run without inference

Serve the already-verified static artifact on loopback:

```powershell
uv run --locked python -m http.server 8766 --bind 127.0.0.1 --directory showcase/public
```

Open `http://127.0.0.1:8766/`; stop with Ctrl+C. Or inspect deterministic outcomes directly:

```powershell
uv run --locked python -m scoperelay.evaluation.offline_scenarios
```

Optional local sandbox (scripted turns by default; data lives outside the checkout):

```powershell
$sandboxData = Join-Path $env:TEMP 'scoperelay-local-sandbox'
uv run --locked python -m scoperelay.sandbox --enable-local-sandbox --data-dir $sandboxData
```

The reviewer workbench and sandbox are distinct local apps. Never expose them to a network or supply real customer data. A live provider path requires separate authorization; this guide does not grant it. Avoid placing API keys in browser-side configuration.

## Troubleshooting

- If `uv` attempts to fetch Python, first install CPython 3.13 separately, then repeat with `--no-python-downloads`.
- If CPython 3.13 is installed but uv cannot discover it, pass the installed executable path explicitly: `uv sync --locked --python 'C:\path\to\Python313\python.exe' --no-python-downloads` (replace the example path with your actual installation).
- If locked sync fails, do not loosen the runtime/dependency constraints to get green; record the resolver/platform error.
- If static verification fails, do not regenerate or edit the frozen manifest to bless changed bytes. Compare the exact named file and the builder's canonical newline rules against the reviewed manifest.
- If a live gate says blocked, leave it blocked unless the provider/endpoint/model/terms/cost authorization has been explicitly resolved.
