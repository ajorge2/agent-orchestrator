import json
import sqlite3
import uuid
from pathlib import Path

DB_PATH = Path.home() / ".orcview" / "runs.db"


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS versions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                version_id  TEXT UNIQUE NOT NULL,
                label       TEXT NOT NULL,
                config      TEXT,
                created_at  TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS runs (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id               TEXT UNIQUE NOT NULL,
                version_id           TEXT,
                question             TEXT,
                agent_label          TEXT DEFAULT 'Agent',
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
        # migrate existing runs tables that predate version_id
        try:
            conn.execute("ALTER TABLE runs ADD COLUMN version_id TEXT")
        except Exception:
            pass


# ── versions ──────────────────────────────────────────────────────────────────

def create_version(label: str, config: dict | None = None) -> str:
    version_id = uuid.uuid4().hex[:8]
    with _connect() as conn:
        conn.execute(
            "INSERT INTO versions (version_id, label, config) VALUES (?,?,?)",
            (version_id, label, json.dumps(config or {})),
        )
    return version_id


def get_latest_version() -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM versions ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


def get_version_run_count(version_id: str) -> int:
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM runs WHERE version_id = ?", (version_id,)
        ).fetchone()
    return row[0] if row else 0


def list_versions() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute("""
            SELECT v.version_id, v.label, v.created_at,
                   COUNT(r.id) AS run_count
            FROM versions v
            LEFT JOIN runs r ON r.version_id = v.version_id
            GROUP BY v.version_id
            ORDER BY v.id DESC
        """).fetchall()
    return [dict(r) for r in rows]


# ── runs ───────────────────────────────────────────────────────────────────────

def save_run(run_id: str, question: str, metrics: dict,
             events: list[dict], version_id: str | None = None) -> None:
    with _connect() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO runs
              (run_id, version_id, question, agent_label, total_input_tokens,
               total_output_tokens, total_cost_usd, total_latency_ms,
               cycles, events, error)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (
            run_id,
            version_id,
            question,
            metrics.get("agent_label", "Agent"),
            metrics.get("total_input_tokens", 0),
            metrics.get("total_output_tokens", 0),
            metrics.get("total_cost_usd", 0.0),
            metrics.get("total_latency_ms", 0),
            metrics.get("cycles", 0),
            json.dumps(events),
            metrics.get("error"),
        ))


def list_runs(limit: int = 50, version_id: str | None = None) -> list[dict]:
    with _connect() as conn:
        if version_id:
            rows = conn.execute("""
                SELECT run_id, version_id, question, agent_label,
                       total_input_tokens, total_output_tokens, total_cost_usd,
                       total_latency_ms, cycles, error, created_at
                FROM runs WHERE version_id = ?
                ORDER BY id DESC LIMIT ?
            """, (version_id, limit)).fetchall()
        else:
            rows = conn.execute("""
                SELECT run_id, version_id, question, agent_label,
                       total_input_tokens, total_output_tokens, total_cost_usd,
                       total_latency_ms, cycles, error, created_at
                FROM runs ORDER BY id DESC LIMIT ?
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
    result["events"] = json.loads(result["events"] or "[]")
    return result
