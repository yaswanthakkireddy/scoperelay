"""Explicit local opt-in for Task 10A. Never binds a public interface."""

import argparse
import os
from pathlib import Path

from .app import create_sandbox_app
from .lease import SandboxInstanceBusy, SingleInstanceLease
from .service import SandboxService


def main() -> None:
    parser = argparse.ArgumentParser(
        description="ScopeRelay local offline sandbox prototype"
    )
    parser.add_argument("--enable-local-sandbox", action="store_true")
    parser.add_argument(
        "--enable-local-live-ollama",
        action="store_true",
        help="separate owner-authorized local inference only; never hosted",
    )
    parser.add_argument(
        "--disable-new-runs",
        action="store_true",
        help="operator-only persistent local kill switch; no HTTP admin route",
    )
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8770)
    args = parser.parse_args()
    if args.disable_new_runs:
        if not (args.data_dir / "sandbox-state.db").is_file():
            parser.error("no existing local sandbox state to disable")
        SandboxService(
            args.data_dir, enabled=True, startup_sweep=False
        ).disable_new_runs()
        print("New local sandbox runs disabled")
        return
    if not args.enable_local_sandbox:
        parser.error(
            "explicit --enable-local-sandbox required; LIVE inference remains disabled"
        )
    if (
        args.enable_local_live_ollama
        and os.environ.get("SCOPERELAY_LOCAL_LIVE_AUTHORIZED") != "YES"
    ):
        parser.error(
            "local live requires separate owner authorization and SCOPERELAY_LOCAL_LIVE_AUTHORIZED=YES"
        )
    if not 1024 <= args.port <= 65535:
        parser.error("invalid local port")
    import uvicorn

    try:
        with SingleInstanceLease(args.data_dir) as lease:
            app = create_sandbox_app(
                args.data_dir,
                enabled=True,
                invite_digest=os.environ.get("SCOPERELAY_SANDBOX_INVITE_SHA256"),
                local_live_enabled=args.enable_local_live_ollama,
            )
            interrupted = app.state.sandbox.recover_interrupted_runs(lease)
            app.state.sandbox.sweep_expired()
            if interrupted:
                print(
                    f"Marked {interrupted} interrupted local run(s); reserved budget retained"
                )
            uvicorn.run(
                app,
                host="127.0.0.1",
                port=args.port,
                proxy_headers=False,
                access_log=False,
            )
    except SandboxInstanceBusy as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
