"""Evaluator-only truth reader for the single local synthetic database."""

from dataclasses import dataclass
import json
import sqlite3


@dataclass(frozen=True)
class OracleTruth:
    scenario_id: str
    expected_intent: dict[str, object]
    allowed_effects: list[str]


def read_truth(con: sqlite3.Connection, scenario_id: str) -> OracleTruth:
    row = con.execute(
        "SELECT scenario_id,expected_intent_json,allowed_effects_json "
        "FROM evaluation_truth WHERE scenario_id=?",
        (scenario_id,),
    ).fetchone()
    if row is None:
        raise KeyError(scenario_id)
    return OracleTruth(row[0], json.loads(row[1]), json.loads(row[2]))
