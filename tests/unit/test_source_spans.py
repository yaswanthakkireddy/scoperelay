import json
from pathlib import Path

import pytest
from hypothesis import given, strategies as st

from scoperelay.storage.source_spans import (
    SourceReferenceError,
    resolve_span,
    source_digest,
)
from scoperelay.storage.database import open_existing
from scoperelay.storage.fixture_loader import seed_synthetic
from scoperelay.storage.schema import initialize


ROOT = Path(__file__).resolve().parents[2]
SOURCES = ROOT / "fixtures" / "scenarios" / "source_texts.json"
ORACLE = ROOT / "fixtures" / "scenarios" / "oracle_labels.json"


def test_eight_synthetic_sources_have_separate_oracle() -> None:
    sources = json.loads(SOURCES.read_text(encoding="utf-8"))["sources"]
    labels = json.loads(ORACLE.read_text(encoding="utf-8"))["labels"]
    assert {row["scenario_id"] for row in sources} == {
        f"SC-{i:02}" for i in range(1, 9)
    }
    assert {row["scenario_id"] for row in labels} == {
        row["scenario_id"] for row in sources
    }
    assert all(row["source_version"] == 1 for row in sources)
    assert all("expected_branch" not in row for row in sources)
    assert all("body_text" not in row for row in labels)


@given(st.text(max_size=100), st.integers(min_value=0, max_value=100))
def test_exact_unicode_code_point_span(body: str, start: int) -> None:
    start = min(start, len(body))
    end = len(body)
    assert (
        resolve_span(
            expected_ticket_id="T",
            expected_source_version=1,
            ticket_id="T",
            source_version=1,
            body_text=body,
            body_sha256=source_digest(body),
            start=start,
            end=end,
            excerpt=body[start:end],
        )
        == body[start:end]
    )


@pytest.mark.parametrize(
    "change",
    [
        {"ticket_id": "wrong"},
        {"source_version": 2},
        {"body_sha256": "0" * 64},
        {"start": -1},
        {"end": 99},
        {"excerpt": "not present"},
        {"start": True},
    ],
)
def test_bad_reference_is_rejected(change: dict) -> None:
    body = "é🙂 status"
    reference = dict(
        expected_ticket_id="T",
        expected_source_version=1,
        ticket_id="T",
        source_version=1,
        body_text=body,
        body_sha256=source_digest(body),
        start=0,
        end=2,
        excerpt="é🙂",
    )
    reference.update(change)
    with pytest.raises(SourceReferenceError):
        resolve_span(**reference)


def test_seeded_sqlite_source_requires_exact_location_not_plausible_excerpt(
    tmp_path,
) -> None:
    path = tmp_path / "source.db"
    initialize(path)
    seed_synthetic(path, SOURCES, ORACLE)
    con = open_existing(path)
    source_id, ticket_id, version, body, digest = con.execute(
        "SELECT source_id,ticket_id,source_version,body_text,body_sha256 "
        "FROM ticket_sources WHERE ticket_id='T-001'"
    ).fetchone()
    con.close()
    excerpt = "ORD-101"
    start = body.index(excerpt)
    valid = dict(
        expected_ticket_id=ticket_id,
        expected_source_version=version,
        ticket_id=ticket_id,
        source_version=version,
        body_text=body,
        body_sha256=digest,
        start=start,
        end=start + len(excerpt),
        excerpt=excerpt,
    )
    assert source_id and resolve_span(**valid) == excerpt
    for change in (
        {"end": start + len(excerpt) + 1},
        {"start": body.index("order")},
        {"source_version": version + 1},
        {"ticket_id": "T-002"},
        {"body_sha256": "0" * 64},
        {"excerpt": "not in the source"},
        {"excerpt": ""},
        {"start": 16, "end": 31},
    ):
        with pytest.raises(SourceReferenceError):
            resolve_span(**{**valid, **change})


def test_repeated_and_unicode_locations_are_code_point_exact() -> None:
    fixture = json.loads(SOURCES.read_text(encoding="utf-8"))["sources"][0]["body_text"]
    body = "🙂e\u0301 " + fixture + " " + fixture
    excerpt = "ORD-101"
    first = body.index(excerpt)
    second = body.rindex(excerpt)
    assert first != second
    assert len(body[:first].encode("utf-8")) != first
    for start in (first, second):
        assert (
            resolve_span(
                expected_ticket_id="T-001",
                expected_source_version=1,
                ticket_id="T-001",
                source_version=1,
                body_text=body,
                body_sha256=source_digest(body),
                start=start,
                end=start + len(excerpt),
                excerpt=excerpt,
            )
            == excerpt
        )
    with pytest.raises(SourceReferenceError):
        resolve_span(
            expected_ticket_id="T-001",
            expected_source_version=1,
            ticket_id="T-001",
            source_version=1,
            body_text=body,
            body_sha256=source_digest(body),
            start=second,
            end=second + len(excerpt),
            excerpt=body[first - 1 : first + len(excerpt)],
        )
    assert body.startswith("🙂e\u0301") and len("e\u0301") == 2
