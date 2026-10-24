from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .dataset import find_sqlite_database, introspect_sqlite_schema
from .execution import execute_sqlite_readonly
from .validation import validate_readonly_sql


DATABASE_ID = re.compile(r"^[A-Za-z0-9_-]+$")


class DatabaseAuthority:
    """The only component allowed to resolve and execute against database files."""

    def __init__(
        self,
        database_root: str | Path,
        *,
        timeout_seconds: float = 5.0,
        max_rows: int = 1_000,
    ) -> None:
        self.database_root = Path(database_root).resolve()
        self.timeout_seconds = timeout_seconds
        self.max_rows = max_rows

    def list_databases(self) -> dict[str, Any]:
        databases: list[str] = []
        if self.database_root.is_dir():
            for child in sorted(self.database_root.iterdir()):
                if not child.is_dir() or not DATABASE_ID.fullmatch(child.name):
                    continue
                if (child / f"{child.name}.sqlite").is_file() or (
                    child / f"{child.name}.db"
                ).is_file():
                    databases.append(child.name)
        return {"databases": databases, "count": len(databases)}

    def schema(self, db_id: str) -> dict[str, Any]:
        database_path = self._database_path(db_id)
        schema = introspect_sqlite_schema(database_path)
        return {
            "db_id": db_id,
            "tables": [
                {"name": table, "columns": list(columns)}
                for table, columns in sorted(schema.items())
            ],
        }

    def validate(self, db_id: str, sql: str) -> dict[str, Any]:
        database_path = self._database_path(db_id)
        validation = validate_readonly_sql(sql, introspect_sqlite_schema(database_path))
        return {
            "db_id": db_id,
            "decision": "allowed" if validation.valid else "blocked",
            "validation": asdict(validation),
            "database_touched": False,
        }

    def execute(self, db_id: str, sql: str) -> dict[str, Any]:
        database_path = self._database_path(db_id)
        schema = introspect_sqlite_schema(database_path)
        validation = validate_readonly_sql(sql, schema)
        if not validation.valid or not validation.normalized_sql:
            receipt = {
                "db_id": db_id,
                "decision": "blocked",
                "validation": asdict(validation),
                "database_touched": False,
                "execution": None,
            }
            return self._stamp(receipt)

        execution = execute_sqlite_readonly(
            database_path,
            validation.normalized_sql,
            timeout_seconds=self.timeout_seconds,
            max_rows=self.max_rows,
        )
        receipt = {
            "db_id": db_id,
            "decision": "executed" if execution.succeeded else "execution_failed",
            "validation": asdict(validation),
            "database_touched": True,
            "execution": asdict(execution),
        }
        return self._stamp(receipt)

    def explain(self, db_id: str, sql: str) -> dict[str, Any]:
        database_path = self._database_path(db_id)
        schema = introspect_sqlite_schema(database_path)
        validation = validate_readonly_sql(sql, schema)
        if not validation.valid or not validation.normalized_sql:
            return self._stamp(
                {
                    "db_id": db_id,
                    "decision": "blocked",
                    "validation": asdict(validation),
                    "database_touched": False,
                    "query_plan": None,
                }
            )
        plan = execute_sqlite_readonly(
            database_path,
            f"EXPLAIN QUERY PLAN {validation.normalized_sql}",
            timeout_seconds=self.timeout_seconds,
            max_rows=self.max_rows,
        )
        return self._stamp(
            {
                "db_id": db_id,
                "decision": "explained" if plan.succeeded else "explain_failed",
                "validation": asdict(validation),
                "database_touched": True,
                "query_plan": asdict(plan),
            }
        )

    def _database_path(self, db_id: str) -> Path:
        if not DATABASE_ID.fullmatch(db_id):
            raise ValueError("Invalid database ID")
        path = find_sqlite_database(self.database_root, db_id).resolve()
        if self.database_root not in path.parents:
            raise ValueError("Database path escaped the configured authority root")
        return path

    @staticmethod
    def _stamp(receipt: dict[str, Any]) -> dict[str, Any]:
        canonical = json.dumps(receipt, sort_keys=True, separators=(",", ":"), default=str)
        return {
            **receipt,
            "receipt_id": hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20],
            "receipt_semantics": (
                "Deterministic trace identifier for this decision; not an immutability claim."
            ),
        }
