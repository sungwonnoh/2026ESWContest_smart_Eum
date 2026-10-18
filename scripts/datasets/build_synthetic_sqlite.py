"""p1/p2 합성 t0 JSONL을 RPi4용 SQLite 이벤트 DB로 만든다."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LEARNING_ENGINE = ROOT / "services" / "learning-engine"
if str(LEARNING_ENGINE) not in sys.path:
    sys.path.insert(0, str(LEARNING_ENGINE))

from synth_t0 import PERSONAS, generate, write_jsonl  # noqa: E402

SCHEMA = """
CREATE TABLE IF NOT EXISTS personas (
    persona_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    generated_start TEXT NOT NULL,
    generated_days INTEGER NOT NULL,
    seed INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS t0_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    persona_id TEXT NOT NULL REFERENCES personas(persona_id),
    version INTEGER NOT NULL,
    sent_ts INTEGER NOT NULL,
    date TEXT NOT NULL,
    type TEXT NOT NULL,
    t0 REAL NOT NULL,
    source TEXT NOT NULL,
    prompted INTEGER NOT NULL,
    duration_sec REAL NOT NULL,
    area TEXT,
    method TEXT,
    confidence REAL,
    awake_areas_json TEXT,
    eat_t0 REAL,
    UNIQUE (persona_id, type, t0)
);
CREATE INDEX IF NOT EXISTS ix_t0_events_persona_time ON t0_events (persona_id, t0);
CREATE INDEX IF NOT EXISTS ix_t0_events_persona_type ON t0_events (persona_id, type, t0);
"""


def insert_events(conn, persona_id, records, start, days, seed):
    persona = PERSONAS[persona_id]
    conn.execute(
        "INSERT INTO personas VALUES (?, ?, ?, ?, ?, ?)",
        (persona_id, persona.key, persona.note, start, days, seed),
    )
    for r in records:
        conn.execute(
            """INSERT INTO t0_events (
                persona_id, version, sent_ts, date, type, t0, source, prompted,
                duration_sec, area, method, confidence, awake_areas_json, eat_t0
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                persona_id, r["version"], r["sent_ts"], r["date"], r["type"],
                r["t0"], r["source"], int(r["prompted"]), r["duration_sec"],
                r.get("area"), r.get("method"), r.get("confidence"),
                None if r.get("awake_areas") is None else json.dumps(r["awake_areas"]),
                r.get("eat_t0"),
            ),
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--jsonl-dir", type=Path)
    parser.add_argument("--start", default="2026-08-17")
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    from datetime import date

    start = date.fromisoformat(args.start)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.jsonl_dir:
        args.jsonl_dir.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        args.output.unlink()

    conn = sqlite3.connect(args.output)
    try:
        conn.executescript(SCHEMA)
        for persona_id in ("p1", "p2"):
            records = generate(PERSONAS[persona_id], start, args.days, args.seed)
            if args.jsonl_dir:
                write_jsonl(records, args.jsonl_dir / f"{persona_id}_t0_log.jsonl")
            insert_events(conn, persona_id, records, args.start, args.days, args.seed)
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    main()
