"""Build a static, allowlisted ScopeRelay showcase. No database is deployed."""

from hashlib import sha256
import json
from pathlib import Path

from scoperelay.evaluation.offline_scenarios import evaluate_all, SOURCE_FILE
from scoperelay.evaluation.trace_manifest import build_manifest


ROOT = Path(__file__).resolve().parents[3]
SHOWCASE = ROOT / "apps" / "showcase"
PUBLIC_OUTPUT = ROOT / "showcase" / "public"
PUBLIC_NAMES = frozenset(
    {
        "index.html",
        "app.css",
        "app.js",
        "demo.js",
        "trace.json",
        "favicon.svg",
        "vercel.json",
        "data.json",
    }
)
PUBLIC_FIELDS = (
    "scenario_id",
    "fixture_version",
    "protocol",
    "run_kind",
    "model_invoked",
    "deterministic_turns",
    "decision",
    "b_status",
    "failure_stage",
    "failure_code",
    "a_tool_correlated",
    "b_tool_correlated",
    "quote_binding",
    "quote_binding_diagnostic",
    "quote_relevance",
    "scripted_quote",
    "derived_span",
    "candidate_present",
    "verification_present",
    "handoff_present",
    "proposal_present",
    "a_status",
    "b_invocation_status",
    "protected_state_unchanged",
    "resource_attempts",
    "queue_effects",
    "journal_events",
    "approval_consumed",
    "judgment",
    "limitation",
)


def _canonical_text_bytes(path: Path) -> bytes:
    """Read UTF-8 text with platform-independent LF newlines."""
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
    return text.encode("utf-8")


def _write_json(path: Path, payload: dict) -> None:
    """Write stable compact JSON with the frozen artifact's CRLF terminator."""
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    path.write_bytes((encoded + "\r\n").encode("utf-8"))


def public_payload() -> dict:
    result = evaluate_all()
    sources = json.loads(SOURCE_FILE.read_text(encoding="utf-8"))["sources"]
    by_id = {row["scenario_id"]: row for row in sources}
    cases = []
    for record in result["scenarios"]:
        item = {key: record[key] for key in PUBLIC_FIELDS}
        source = by_id[item["scenario_id"]]
        item["source_text"] = source["body_text"]
        item["source_version"] = source["source_version"]
        item["ticket_id"] = source["ticket_id"]
        item["source_sha256"] = sha256(source["body_text"].encode("utf-8")).hexdigest()
        cases.append(item)
    return {
        "schema": "scoperelay-public-offline-v1",
        "fixture_sha256": sha256(_canonical_text_bytes(SOURCE_FILE)).hexdigest(),
        "mode": "DETERMINISTIC_COMPONENT_TEST",
        "model_generations": 0,
        "protected_attempts": 0,
        "accepted_effects": 0,
        "cases": cases,
    }


def build(destination: Path = PUBLIC_OUTPUT) -> Path:
    if destination.exists() and not destination.is_dir():
        raise ValueError("showcase output must be a directory")
    destination.mkdir(parents=True, exist_ok=True)
    # Do not let a prior preview/debug file survive into the next deployment payload.
    unexpected = sorted(
        path.name
        for path in destination.iterdir()
        if path.name not in PUBLIC_NAMES or not path.is_file() or path.is_symlink()
    )
    if unexpected:
        raise ValueError(f"showcase output contains unreviewed entries: {unexpected}")
    for name in (
        "index.html",
        "app.css",
        "app.js",
        "demo.js",
        "favicon.svg",
        "vercel.json",
    ):
        source = SHOWCASE / "src" / name
        (destination / name).write_bytes(_canonical_text_bytes(source))
    payload = public_payload()
    _write_json(destination / "data.json", payload)
    _write_json(destination / "trace.json", build_manifest())
    return destination
