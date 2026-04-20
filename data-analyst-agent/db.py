import json
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "runs.db"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS runs (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id               TEXT UNIQUE NOT NULL,
                question             TEXT,
                agent_label          TEXT DEFAULT 'Agent',
                state_path           TEXT,
                total_input_tokens   INTEGER,
                total_output_tokens  INTEGER,
                total_cost_usd       REAL,
                total_latency_ms     INTEGER,
                cycles               INTEGER,
                events               TEXT,
                error                TEXT,
                created_at           TEXT DEFAULT (datetime('now'))
            )
        """)
        # migrate existing DBs that predate agent_label
        try:
            conn.execute("ALTER TABLE runs ADD COLUMN agent_label TEXT DEFAULT 'Agent'")
        except Exception:
            pass


def save_run(run_id: str, question: str, metrics: dict,
             events: list[dict]) -> None:
    with _connect() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO runs
              (run_id, question, agent_label, state_path, total_input_tokens,
               total_output_tokens, total_cost_usd, total_latency_ms,
               cycles, events, error)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (
            run_id,
            question,
            metrics.get("agent_label", "Agent"),
            json.dumps(metrics.get("state_path", [])),
            metrics.get("total_input_tokens", 0),
            metrics.get("total_output_tokens", 0),
            metrics.get("total_cost_usd", 0.0),
            metrics.get("total_latency_ms", 0),
            metrics.get("cycles", 0),
            json.dumps(events),
            metrics.get("error"),
        ))


def list_runs(limit: int = 50) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute("""
            SELECT run_id, question, agent_label, state_path, total_input_tokens,
                   total_output_tokens, total_cost_usd, total_latency_ms,
                   cycles, error, created_at
            FROM runs
            ORDER BY id DESC
            LIMIT ?
        """, (limit,)).fetchall()
    return [dict(r) for r in rows]


def get_run(run_id: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
    if not row:
        return None
    result = dict(row)
    result["events"]     = json.loads(result["events"] or "[]")
    result["state_path"] = json.loads(result["state_path"] or "[]")
    return result
