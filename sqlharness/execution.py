from __future__ import annotations

import math
import sqlite3
import time
from collections import Counter
from pathlib import Path
from typing import Any

from .types import ExecutionResult


def execute_sqlite_readonly(
    database_path: str | Path,
    sql: str,
    *,
    timeout_seconds: float = 5.0,
    max_rows: int = 10_000,
) -> ExecutionResult:
    path = Path(database_path).resolve()
    uri = f"file:{path.as_posix()}?mode=ro"
    started = time.perf_counter()
    deadline = started + timeout_seconds

    try:
        with sqlite3.connect(uri, uri=True, timeout=timeout_seconds) as connection:
            # Bound allocations before SQLite evaluates a large scalar/blob.
            connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 1_000_000)
            connection.execute("PRAGMA query_only = ON")

            def interrupt_if_expired() -> int:
                return int(time.perf_counter() >= deadline)

            connection.set_progress_handler(interrupt_if_expired, 1_000)
            cursor = connection.execute(sql)
            columns = tuple(description[0] for description in cursor.description or ())
            fetched = cursor.fetchmany(max_rows + 1)
            truncated = len(fetched) > max_rows
            rows = tuple(tuple(_normalize_cell(value) for value in row) for row in fetched[:max_rows])
            return ExecutionResult(
                succeeded=True,
                rows=rows,
                columns=columns,
                latency_ms=(time.perf_counter() - started) * 1000,
                truncated=truncated,
            )
    except sqlite3.Error as error:
        return ExecutionResult(
            succeeded=False,
            latency_ms=(time.perf_counter() - started) * 1000,
            error=str(error),
        )


def results_equivalent(
    candidate: ExecutionResult,
    gold: ExecutionResult,
    *,
    order_sensitive: bool,
) -> bool:
    if not candidate.succeeded or not gold.succeeded:
        return False
    if candidate.truncated or gold.truncated:
        return False
    if len(candidate.columns) != len(gold.columns):
        return False
    if order_sensitive:
        return candidate.rows == gold.rows
    return Counter(candidate.rows) == Counter(gold.rows)


def sql_requires_order(sql: str) -> bool:
    try:
        import sqlglot
        from sqlglot import exp
    except ImportError as error:
        raise RuntimeError("Order detection requires the sqlglot dependency") from error
    expression = sqlglot.parse_one(sql)
    return expression.find(exp.Order) is not None


def _normalize_cell(value: Any) -> Any:
    if isinstance(value, float):
        if math.isnan(value):
            return "NaN"
        return round(value, 10)
    if isinstance(value, bytes):
        return value.hex()
    return value

