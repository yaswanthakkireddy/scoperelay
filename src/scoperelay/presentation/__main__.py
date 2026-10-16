"""Explicit disposable fixture bootstrap and loopback-only workbench startup."""

from pathlib import Path
import argparse

from scoperelay.storage.fixture_loader import seed_synthetic
from scoperelay.storage.schema import initialize
from .app import create_app


def main() -> None:
    parser = argparse.ArgumentParser(
        description="ScopeRelay synthetic reviewer workbench"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser(
        "init-demo", help="create a NEW disposable synthetic database"
    )
    init.add_argument("--db", type=Path, required=True)
    serve = commands.add_parser(
        "serve", help="serve the existing database read-only on loopback"
    )
    serve.add_argument("--db", type=Path, required=True)
    serve.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    path = args.db.resolve(strict=False)
    if args.command == "init-demo":
        if path.exists():
            parser.error("init-demo refuses an existing database")
        if not path.parent.is_dir():
            parser.error("create the chosen disposable directory first")
        fixture = Path.cwd() / "fixtures" / "scenarios"
        if (
            not (fixture / "source_texts.json").is_file()
            or not (fixture / "oracle_labels.json").is_file()
        ):
            parser.error(
                "run init-demo from the repository root with canonical fixtures"
            )
        initialize(path)
        seed_synthetic(
            path, fixture / "source_texts.json", fixture / "oracle_labels.json"
        )
        print(f"Created synthetic fixture: {path}")
    else:
        if not 1024 <= args.port <= 65535:
            parser.error("port must be between 1024 and 65535")
        import uvicorn

        uvicorn.run(
            create_app(path),
            host="127.0.0.1",
            port=args.port,
            proxy_headers=False,
            access_log=False,
        )


if __name__ == "__main__":
    main()
