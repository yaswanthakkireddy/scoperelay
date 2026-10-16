"""Explicit opt-in synthetic LIVE capability smoke. Never run from pytest/CI."""

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
from time import monotonic

from scoperelay.inference.adapter import ProviderAdapter
from scoperelay.storage.database import open_existing
from scoperelay.storage.fixture_loader import EPOCH, seed_synthetic
from scoperelay.storage.schema import initialize

from .read_only_agents import AgentExecutionError, ReadOnlyAgentRunner


_PROTECTED_TABLES = (
    "database_identity",
    "scenarios",
    "organizations",
    "principals",
    "operator_sessions",
    "org_policy",
    "manager_grants",
    "customers",
    "accounts",
    "contacts",
    "orders",
    "tickets",
    "ticket_sources",
    "evaluation_truth",
    "material_actions",
    "escalation_approvals",
    "resource_attempts",
    "queue_effects",
    "journal_events",
    "original_actions",
    "reconciliation_events",
)


def _protected_snapshot(path: Path) -> tuple[str, dict[str, int]]:
    """Digest only immutable/trusted/effect state; agent evidence is excluded."""
    con = open_existing(path)
    try:
        digest = sha256()
        counts = {}
        for table in _PROTECTED_TABLES:  # fixed names, never model-supplied SQL
            rows = [
                tuple(row)
                for row in con.execute(f"SELECT * FROM {table} ORDER BY rowid")
            ]
            counts[table] = len(rows)
            digest.update(
                json.dumps(
                    [table, rows],
                    ensure_ascii=False,
                    default=str,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
        return digest.hexdigest(), counts
    finally:
        con.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Opt-in, synthetic-only read-only A/B capability smoke"
    )
    parser.add_argument("--confirm-lawful-no-cost-tool-use", action="store_true")
    parser.add_argument(
        "--output-db",
        type=Path,
        help="New, nonexistent local SQLite evidence path; never overwrites a DB",
    )
    args = parser.parse_args(argv)
    if not args.confirm_lawful_no_cost_tool_use:
        print("LIVE_PROVIDER_BLOCKED: explicit lawful no-cost confirmation required")
        return 2
    names = (
        "SCOPERELAY_LIVE_PROVIDER",
        "SCOPERELAY_LIVE_MODEL",
        "SCOPERELAY_LIVE_BASE_URL",
        "SCOPERELAY_LIVE_API_KEY",
    )
    if any(not os.environ.get(name) for name in names):
        print("LIVE_PROVIDER_BLOCKED: trusted provider configuration incomplete")
        return 2
    if (
        args.output_db is None
        or args.output_db.exists()
        or not args.output_db.parent.is_dir()
    ):
        print(
            "LIVE_PROVIDER_BLOCKED: provide a new SQLite path in an existing local directory"
        )
        return 2
    provider, model, base_url, api_key = (os.environ[name] for name in names)
    root = Path(__file__).resolve().parents[3]
    path = args.output_db.resolve()
    initialize(path)
    seed_synthetic(
        path,
        root / "fixtures/scenarios/source_texts.json",
        root / "fixtures/scenarios/oracle_labels.json",
    )
    con = open_existing(path)
    try:
        con.execute(
            "INSERT INTO episodes VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                "live-smoke-sc01",
                "SC-01",
                EPOCH,
                "T-001",
                1,
                "grant-01",
                "LIVE",
                "STARTED",
                datetime.now(timezone.utc).isoformat(),
                None,
            ),
        )
        con.execute(
            "INSERT INTO episode_candidate_protocols VALUES (?,?,?)",
            (
                "live-smoke-sc01",
                "V2_EXACT_QUOTE",
                datetime.now(timezone.utc).isoformat(),
            ),
        )
    finally:
        con.close()
    local = provider == "ollama-local"
    adapter = ProviderAdapter(
        provider=provider,
        model=model,
        base_url=base_url,
        api_key=api_key,
        authorized=True,
        timeout=240 if local else 15,
        max_requests=6,
    )
    before = _protected_snapshot(path)
    started = monotonic()
    try:
        result = ReadOnlyAgentRunner(
            path,
            adapter,
            max_tokens=1024 if local else 256,
            max_duration_seconds=900 if local else 60,
        ).run("live-smoke-sc01")
    except AgentExecutionError as exc:
        result = None
        print(f"LIVE_CAPABILITY_FAILED: {type(exc).__name__}; evidence DB={path}")
    after = _protected_snapshot(path)
    print(
        f"LIVE_REQUESTS={adapter.request_count}; generation_seconds="
        f"{[round(t, 2) for t in adapter.request_seconds]}; "
        f"total_seconds={round(monotonic() - started, 2)}"
    )
    print(
        f"PROTECTED_STATE_UNCHANGED={before == after}; "
        f"before_sha256={before[0]}; after_sha256={after[0]}; "
        f"resource_attempts={after[1]['resource_attempts']}; "
        f"queue_effects={after[1]['queue_effects']}; "
        f"journal_events={after[1]['journal_events']}"
    )
    if before != after:
        print("READ_ONLY_VIOLATION: protected/trusted snapshot changed")
        return 3
    if result is None:
        return 1
    print(
        f"LIVE_CAPABILITY_RESULT: A={result.a_invocation_id} B={result.b_invocation_id} "
        f"decision={result.decision} B_status={result.b_status} effect={result.protected_effect} "
        f"evidence DB={path}"
    )
    return 0 if result.b_status == "PROPOSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
