"""Public export and reviewer routes cannot promote offline data to authority."""

from hashlib import sha256
from html import unescape
import json
from pathlib import Path

from fastapi.testclient import TestClient

from scoperelay.presentation.app import create_app
from scoperelay.presentation import read_models
from scoperelay.storage.database import open_existing
from scoperelay.storage.fixture_loader import seed_synthetic
from scoperelay.storage.schema import initialize
from scoperelay.evaluation.public_showcase import PUBLIC_FIELDS, build, public_payload
import pytest


ROOT = Path(__file__).resolve().parents[2]


def _db(tmp_path: Path) -> Path:
    path = tmp_path / "review.db"
    initialize(path)
    seed_synthetic(
        path,
        ROOT / "fixtures/scenarios/source_texts.json",
        ROOT / "fixtures/scenarios/oracle_labels.json",
    )
    return path


def _build_offline_export(tmp_path: Path, monkeypatch) -> Path:
    """Build the public offline fixture in this test's isolated temp directory."""
    output = build(tmp_path / "showcase" / "public")
    export = output / "data.json"
    monkeypatch.setattr(read_models, "_OFFLINE_EXPORT", export)
    return export


def test_public_export_is_allowlisted_offline_and_source_bound():
    payload = public_payload()
    assert payload["schema"] == "scoperelay-public-offline-v1"
    assert payload["mode"] == "DETERMINISTIC_COMPONENT_TEST"
    assert payload["model_generations"] == 0
    assert payload["protected_attempts"] == payload["accepted_effects"] == 0
    assert len(payload["cases"]) == 8
    for row in payload["cases"]:
        assert set(row) == set(PUBLIC_FIELDS) | {
            "source_text",
            "source_version",
            "ticket_id",
            "source_sha256",
        }
        assert row["source_sha256"] == sha256(row["source_text"].encode()).hexdigest()
        assert row["run_kind"] == "DETERMINISTIC_COMPONENT_TEST"
        assert row["model_invoked"] is False
        assert (
            row["resource_attempts"],
            row["queue_effects"],
            row["journal_events"],
        ) == (0, 0, 0)
    text = json.dumps(payload)
    for forbidden in (
        "expected_branch",
        "allowed_effects",
        "evaluation_truth",
        "supervisor_id",
        "api_key",
        "NVIDIA_API_KEY",
        "C:\\Users\\",
        "D:\\Tollgate",
    ):
        assert forbidden not in text
    by_id = {row["scenario_id"]: row for row in payload["cases"]}
    assert by_id["SC-06"]["judgment"] == "PREAPPROVAL_ONLY"
    assert by_id["SC-06"]["approval_consumed"] is False
    assert by_id["SC-08"]["judgment"] == "SEMANTIC_RESIDUAL_PROPOSAL"
    assert by_id["SC-08"]["b_status"] == "PROPOSED"


def test_static_build_is_deployable_and_has_no_privileged_routes(tmp_path):
    output = build(tmp_path / "public")
    assert {p.name for p in output.iterdir()} == {
        "index.html",
        "app.css",
        "app.js",
        "demo.js",
        "trace.json",
        "favicon.svg",
        "vercel.json",
        "data.json",
    }
    assert (
        json.loads((output / "data.json").read_text(encoding="utf-8"))
        == public_payload()
    )
    html = (output / "index.html").read_text(encoding="utf-8")
    js = (output / "app.js").read_text(encoding="utf-8")
    headers = json.loads((output / "vercel.json").read_text(encoding="utf-8"))[
        "headers"
    ][0]["headers"]
    assert 'id="case-search"' in html and 'id="case-list"' in html
    assert 'id="case-detail"' in html and "SC-08" in html
    assert 'id="live-evidence"' in html and "Four completed generation requests" in html
    assert "textContent" in js and "innerHTML" not in js and "eval(" not in js
    assert "api-108" not in js.lower() and "localhost" not in js
    assert any(
        item["key"] == "Content-Security-Policy"
        and "default-src 'none'" in item["value"]
        for item in headers
    )
    assert any(
        item["key"] == "Cache-Control" and "must-revalidate" in item["value"]
        for item in headers
    )
    for file in output.iterdir():
        assert file.suffix not in {".db", ".sqlite", ".env", ".py"}


def test_static_build_canonicalizes_windows_newlines_and_matches_release_manifest(
    tmp_path,
):
    output = build(tmp_path / "public")
    manifest_path = ROOT / "docs/delivery/STATIC_SHOWCASE_RELEASE_MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    actual = {
        path.name: sha256(path.read_bytes()).hexdigest()
        for path in output.iterdir()
        if path.is_file()
    }
    assert actual == manifest["files_sha256"]

    fixture = ROOT / "fixtures/scenarios/source_texts.json"
    canonical_fixture = (
        fixture.read_text(encoding="utf-8")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .encode("utf-8")
    )
    assert public_payload()["fixture_sha256"] == sha256(canonical_fixture).hexdigest()
    copied_assets = {
        "index.html",
        "app.css",
        "app.js",
        "demo.js",
        "favicon.svg",
        "vercel.json",
    }
    assert all(b"\r\n" not in (output / name).read_bytes() for name in copied_assets)
    assert (output / "data.json").read_bytes().endswith(b"\r\n")
    assert (output / "trace.json").read_bytes().endswith(b"\r\n")


def test_static_build_rejects_stale_unreviewed_file(tmp_path):
    output = build(tmp_path / "public")
    (output / "debug.log").write_text("private debug output", encoding="utf-8")
    with pytest.raises(ValueError, match="unreviewed entries:.*debug.log"):
        build(output)
    assert (output / "debug.log").read_text(encoding="utf-8") == "private debug output"


def test_local_offline_page_is_read_only_and_not_live(tmp_path, monkeypatch):
    _build_offline_export(tmp_path, monkeypatch)
    path = _db(tmp_path)
    before = read_models.resource_history(path)
    with TestClient(create_app(path), base_url="http://127.0.0.1:8765") as web:
        page = web.get("/evaluation")
        assert page.status_code == 200
        assert "Eight scripted cases" in page.text
        assert "NO-GO" in page.text
        assert "SC-08" in page.text and "SEMANTIC RESIDUAL PROPOSAL" in page.text
        assert "SC-06" in page.text and "NOT VERIFIED IN THIS EVALUATION" in page.text
        assert "genuine LIVE inference" not in page.text
        assert web.get("/scenarios/SC-08").status_code == 200
        assert "FIXTURE-MATCHED OFFLINE SNAPSHOT" in web.get("/scenarios/SC-08").text
        assert "OFFLINE: SEMANTIC RESIDUAL PROPOSAL" in unescape(
            web.get("/scenarios").text
        )
        assert web.post("/evaluation", json={"approve": True}).status_code == 405
        assert web.get("/evaluation?role=SUPERVISOR").status_code == 400
    assert read_models.resource_history(path) == before


def test_mismatched_source_omits_stale_offline_result(tmp_path, monkeypatch):
    _build_offline_export(tmp_path, monkeypatch)
    path = _db(tmp_path)
    con = open_existing(path)
    try:
        con.execute(
            "UPDATE tickets SET current_source_version=2 WHERE ticket_id='T-008'"
        )
    finally:
        con.close()
    result = read_models.offline_evaluation(path)
    assert result["available"] is True
    assert all(row["scenario_id"] != "SC-08" for row in result["cases"])


def test_missing_offline_export_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(
        read_models, "_OFFLINE_EXPORT", tmp_path / "missing" / "data.json"
    )
    path = _db(tmp_path)

    assert read_models.offline_evaluation(path) == {"available": False, "cases": []}
    with TestClient(create_app(path), base_url="http://127.0.0.1:8765") as web:
        page = web.get("/evaluation")
        assert page.status_code == 200
        assert "No public offline export built" in page.text


def test_hostile_source_is_inert_in_evaluation_navigation(tmp_path):
    source_doc = json.loads(
        (ROOT / "fixtures/scenarios/source_texts.json").read_text(encoding="utf-8")
    )
    source_doc["sources"][3]["body_text"] += '<img src=x onerror="alert(1)">'
    source_file = tmp_path / "source.json"
    source_file.write_text(json.dumps(source_doc), encoding="utf-8")
    path = tmp_path / "hostile.db"
    initialize(path)
    seed_synthetic(path, source_file, ROOT / "fixtures/scenarios/oracle_labels.json")
    with TestClient(create_app(path), base_url="http://127.0.0.1:8765") as web:
        html = web.get("/scenarios").text
        assert "&lt;img" in html and '<img src=x onerror="alert(1)">' not in html
        assert "OFFLINE: BOUNDARY REJECTED" not in unescape(
            web.get("/scenarios/SC-04").text
        )


def test_corrupt_or_false_live_export_is_not_displayed(tmp_path, monkeypatch):
    path = _db(tmp_path)
    payload = public_payload()
    payload["cases"][0]["run_kind"] = "LIVE"
    export = tmp_path / "data.json"
    export.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(read_models, "_OFFLINE_EXPORT", export)
    assert read_models.offline_evaluation(path) == {"available": False, "cases": []}
    with TestClient(create_app(path), base_url="http://127.0.0.1:8765") as web:
        assert "No public offline export built" in web.get("/evaluation").text
        assert "LIVE recorded" not in web.get("/evaluation").text
