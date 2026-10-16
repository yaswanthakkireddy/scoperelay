"""Historical replay export is supported by Task 07G, never by offline SC-01."""

from hashlib import sha256
import json

import pytest

from scoperelay.evaluation.trace_manifest import build_manifest
from scoperelay.evaluation.public_showcase import build


def test_manifest_binds_exact_original_source_and_report():
    item = build_manifest()
    assert item["schema"] == "scoperelay-recorded-checkpoints-v1"
    assert item["classification"] == "RECORDED_REPLAY"
    assert item["historical_run_kind"] == "LIVE"
    assert item["playback_is_inference"] is False
    assert (item["historical_generations"], item["new_generations"]) == (4, 0)
    source = item["source"]
    assert (source["ticket_id"], source["source_id"], source["version"]) == (
        "T-001",
        "source-01",
        1,
    )
    assert sha256(source["text"].encode()).hexdigest() == source["sha256"]
    span = source["trusted_span"]
    assert (span["start"], span["end"], span["unit"]) == (0, 72, "Unicode code points")
    assert source["text"][span["start"] : span["end"]] == source["model_selected_quote"]
    assert source["text"].count(source["model_selected_quote"]) == 1
    assert len(item["stages"]) == 10
    assert all(
        stage["record_type"] == "REPORT_BACKED_CHECKPOINT" for stage in item["stages"]
    )
    by_stage = {stage["id"]: stage for stage in item["stages"]}
    assert "call_58ql716p" in str(by_stage["a_call"]["facts"])
    assert "3fc46516e1094c8c955c05818b80f218" in str(by_stage["b_call"]["facts"])
    assert "Not retained in public evidence" in str(by_stage["a_continue"]["facts"])
    assert item["protected_resource"] == "NOT_ATTEMPTED"
    assert "Full raw Agent A continuation" in item["unavailable"]


def test_report_drift_fails_closed(monkeypatch, tmp_path):
    report = tmp_path / "report.md"
    report.write_text(
        "Task 07G summary without corroborating markers", encoding="utf-8"
    )
    monkeypatch.setattr("scoperelay.evaluation.trace_manifest.REPORT", report)
    with pytest.raises(ValueError, match="evidence markers missing"):
        build_manifest()


def test_public_artifact_separates_replay_and_offline(tmp_path):
    output = build(tmp_path / "public")
    trace = json.loads((output / "trace.json").read_text(encoding="utf-8"))
    offline = json.loads((output / "data.json").read_text(encoding="utf-8"))
    assert trace == build_manifest()
    assert offline["mode"] == "DETERMINISTIC_COMPONENT_TEST"
    assert all(case["model_invoked"] is False for case in offline["cases"])
    assert [case["scenario_id"] for case in offline["cases"]] == [
        f"SC-{n:02}" for n in range(1, 9)
    ]
    assert offline["cases"][5]["judgment"] == "PREAPPROVAL_ONLY"
    assert offline["cases"][7]["judgment"] == "SEMANTIC_RESIDUAL_PROPOSAL"
    html = (output / "index.html").read_text(encoding="utf-8")
    assert "Run Verified Demo" in html and "Explore Security Cases" in html
    assert "OFFLINE" in html and "no new model request" in html
    exported = "\n".join(
        path.read_text(encoding="utf-8")
        for path in output.iterdir()
        if path.suffix in {".html", ".js", ".json", ".css", ".svg"}
    )
    for forbidden in (
        "gsk_",
        "GROQ_API_KEY",
        "sk-nvapi-",
        "C:\\Users\\",
        "D:\\Tollgate",
        "api.groq.com",
        "oracle_labels",
        "evaluation_truth",
    ):
        assert forbidden.lower() not in exported.lower()
    scripts = (output / "demo.js").read_text(encoding="utf-8") + (
        output / "app.js"
    ).read_text(encoding="utf-8")
    assert "/api/" not in scripts and "api-108" not in scripts.lower()
    assert 'fetch("./trace.json"' in (output / "demo.js").read_text(encoding="utf-8")
    assert 'fetch("./data.json"' in (output / "app.js").read_text(encoding="utf-8")
