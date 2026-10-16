"""Sanitized checkpoint export of the verified, historical Task 07G run.

This is not a serialization of the private SQLite trace. Each checkpoint below
is explicitly supported by the reviewed Task 07G report; unavailable raw text
is deliberately not reconstructed.
"""

from hashlib import sha256
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
REPORT = ROOT / "docs/reports/TASK07G_STRUCTURED_LIVE_VERIFICATION_REPORT.md"
SOURCES = ROOT / "fixtures/scenarios/source_texts.json"
SOURCE_DIGEST = "bf1ebfc9ca74c99714d35004c4449e3d97b99dabac0b57564739e8a717c692a7"
QUOTE = "Hello, could you tell me the current status of my order ORD-101? Thanks!"


def build_manifest() -> dict:
    report = REPORT.read_text(encoding="utf-8")
    rows = json.loads(SOURCES.read_text(encoding="utf-8"))["sources"]
    source = next(row for row in rows if row["scenario_id"] == "SC-01")
    body = source["body_text"]
    digest = sha256(body.encode("utf-8")).hexdigest()
    if (source["ticket_id"], source["source_version"], digest) != (
        "T-001",
        1,
        SOURCE_DIGEST,
    ):
        raise ValueError(
            "Task 07G source identity/version/digest no longer matches fixture"
        )
    if body.count(QUOTE) != 1 or body.index(QUOTE) != 0 or len(QUOTE) != 72:
        raise ValueError("Task 07G exact quotation is not uniquely bound at [0,72)")
    required = (
        "TASK07G_FULL_LOCAL_TWO_AGENT_VERIFIED",
        "Exactly **4/6**",
        "gemma4:e4b",
        "ef64428d41114418a1b69d87b1b60389",
        "call_58ql716p",
        "73388dd106ef47ea8d1ef2d0b89d7211",
        "VALID_V2_STRUCTURE",
        SOURCE_DIGEST,
        "**`[0,72)`**",
        "EXACT_UNIQUE_V1",
        "**`ALLOW`**",
        "f0fad53ad8b94fad93fe49d0848fb852",
        "e649a7dec7fe4a75b47ff52e2fba022e",
        "call_ng781lob",
        "3fc46516e1094c8c955c05818b80f218",
        "`PROPOSED`",
        "0** material actions",
        "no raw full continuation text was retained",
    )
    missing = [marker for marker in required if marker not in report]
    if missing:
        raise ValueError(f"Task 07G evidence markers missing: {missing}")

    # Ordered report-backed checkpoints, not synthetic tool events or invented dialogue.
    stages = [
        (
            "source",
            "Customer request",
            "CUSTOMER_UNTRUSTED",
            "Synthetic T-001 source v1 was pinned to the episode.",
            "A–D / E–G",
        ),
        (
            "a_call",
            "Agent A reads ticket",
            "MODEL_REQUEST / TRUSTED_TOOL",
            "Agent A invoked read_assigned_ticket for T-001; the trusted dispatcher returned the immutable customer source to the same conversation.",
            "E–G",
        ),
        (
            "a_continue",
            "Agent A continues",
            "MODEL_OUTPUT",
            "A separate post-tool model response completed. Full raw continuation text was not retained in public evidence.",
            "E–G",
        ),
        (
            "binding",
            "Quotation bound",
            "TRUSTED_VALIDATION",
            "A selected the exact quotation. Trusted code validated V2 structure, unique source occurrence and relevance; it derived [0,72).",
            "E–G",
        ),
        (
            "verification",
            "Business facts verified",
            "TRUSTED_APPLICATION",
            "Independent grant, assignment, order and contact checks returned ALLOW and a verified status scope.",
            "H–K",
        ),
        (
            "handoff",
            "H1–H4 handoff",
            "TRUSTED_APPLICATION",
            "A valid handoff kept independent grant and verified facts separate from lower-trust candidate and source references.",
            "H–K",
        ),
        (
            "b_call",
            "Agent B reads status",
            "MODEL_REQUEST / TRUSTED_TOOL",
            "A distinct B invocation called read_verified_order_status; trusted SQLite-backed feedback returned ORD-101 status and customer-visible ETA.",
            "H–K",
        ),
        (
            "b_continue",
            "Agent B continues",
            "MODEL_OUTPUT",
            "The status observation was delivered to the same B conversation before a separate model continuation. Raw full response text was not retained in public evidence.",
            "H–K",
        ),
        (
            "proposal",
            "Bounded proposal",
            "VALIDATED_MODEL_PROPOSAL",
            "B proposed STATUS_BRIEF / STATUS_T1 with only order_id, order_status and customer_visible_eta; its observation reference matched the trusted tool event.",
            "H–K",
        ),
        (
            "no_effect",
            "No protected effect",
            "PROTECTED_STATE_CHECK",
            "No API-108/API-109 call, resource attempt, queue effect, approval consumption or external delivery occurred.",
            "L–R",
        ),
    ]
    evidence_items = {
        "source": [
            ("Episode", "live-smoke-sc01"),
            ("Protocol", "V2_EXACT_QUOTE"),
            ("Source", "source-01 / T-001 / v1"),
        ],
        "a_call": [
            ("A invocation", "ef64428d41114418a1b69d87b1b60389"),
            ("Provider tool call", "call_58ql716p · read_assigned_ticket(T-001)"),
            ("Trusted tool event", "73388dd106ef47ea8d1ef2d0b89d7211 · SUCCESS"),
        ],
        "a_continue": [
            ("Provider turn", "chatcmpl-541 · stop"),
            ("Structure", "VALID_V2_STRUCTURE"),
            ("Raw text", "Not retained in public evidence"),
        ],
        "binding": [
            ("Model selected", QUOTE),
            ("Trusted binding", "EXACT_UNIQUE_V1 · VALIDATED · PASSED"),
            ("Derived span", "[0,72) Unicode code points"),
        ],
        "verification": [
            ("Decision", "ALLOW"),
            ("Verified order", "ORD-101 · IN_TRANSIT · ETA 2026-10-20"),
            ("Verified contact", "contact-c1 · active"),
        ],
        "handoff": [
            ("Handoff", "f0fad53ad8b94fad93fe49d0848fb852 · VALID"),
            ("H1/H2", "Independent grant and verified facts"),
            ("H3/H4", "Lower-trust candidate and checked source reference"),
        ],
        "b_call": [
            ("B invocation", "e649a7dec7fe4a75b47ff52e2fba022e"),
            ("Provider tool call", "call_ng781lob · read_verified_order_status"),
            ("Trusted tool event", "3fc46516e1094c8c955c05818b80f218 · SUCCESS"),
        ],
        "b_continue": [
            ("Provider turn", "chatcmpl-463 · stop"),
            ("Observation delivery", "Same B conversation before turn 2"),
            ("Raw text", "Not retained in public evidence"),
        ],
        "proposal": [
            ("Status", "PROPOSED, not sent"),
            ("Template", "STATUS_BRIEF / STATUS_T1"),
            ("Allowlisted fields", "order_id, order_status, customer_visible_eta"),
        ],
        "no_effect": [
            ("Resource", "NOT_ATTEMPTED"),
            (
                "Protected records",
                "0 attempts · 0 queue effects · 0 approvals consumed",
            ),
        ],
    }
    return {
        "schema": "scoperelay-recorded-checkpoints-v1",
        "classification": "RECORDED_REPLAY",
        "origin": "TASK07G_STRUCTURED_LIVE_VERIFICATION_REPORT.md",
        "scenario_id": "SC-01",
        "historical_run_kind": "LIVE",
        "playback_is_inference": False,
        "runtime": "local Ollama, CPU-only",
        "model": "gemma4:e4b",
        "historical_generations": 4,
        "new_generations": 0,
        "source": {
            "ticket_id": "T-001",
            "source_id": "source-01",
            "version": 1,
            "sha256": digest,
            "text": body,
            "model_selected_quote": QUOTE,
            "trusted_span": {"start": 0, "end": 72, "unit": "Unicode code points"},
        },
        "unavailable": [
            "Full raw Agent A continuation",
            "Full raw Agent B continuation",
            "Private tool payload bodies and full model prompts",
        ],
        "protected_resource": "NOT_ATTEMPTED",
        "stages": [
            {
                "id": stage_id,
                "title": title,
                "trust": trust,
                "summary": summary,
                "evidence": f"Task 07G report §{section}",
                "record_type": "REPORT_BACKED_CHECKPOINT",
                "facts": [
                    {"label": label, "value": value}
                    for label, value in evidence_items[stage_id]
                ],
            }
            for stage_id, title, trust, summary, section in stages
        ],
    }
