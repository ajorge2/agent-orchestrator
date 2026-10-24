from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from .types import BirdExample


def load_bird_examples(path: str | Path) -> list[BirdExample]:
    dataset_path = Path(path)
    payload = _load_payload(dataset_path)
    examples: list[BirdExample] = []

    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            raise ValueError(f"Example {index} must be a JSON object")

        db_id = _required_string(item, "db_id", index)
        question = _required_string(item, "question", index)
        sql = item.get("SQL", item.get("sql"))
        if not isinstance(sql, str) or not sql.strip():
            raise ValueError(f"Example {index} is missing SQL/sql")

        example_id = str(item.get("question_id", item.get("id", f"{db_id}:{index}")))
        evidence = item.get("evidence", "")
        if evidence is None:
            evidence = ""
        if not isinstance(evidence, str):
            evidence = json.dumps(evidence, ensure_ascii=False, sort_keys=True)

        examples.append(
            BirdExample(
                example_id=example_id,
                db_id=db_id,
                question=question.strip(),
                evidence=evidence.strip(),
                gold_sql=sql.strip(),
                difficulty=str(item.get("difficulty", "")).strip(),
            )
        )

    return examples


def find_sqlite_database(database_root: str | Path, db_id: str) -> Path:
    root = Path(database_root)
    candidates = (
        root / db_id / f"{db_id}.sqlite",
        root / db_id / f"{db_id}.db",
        root / f"{db_id}.sqlite",
        root / f"{db_id}.db",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    rendered = "\n".join(f"- {candidate}" for candidate in candidates)
    raise FileNotFoundError(f"No SQLite database found for {db_id!r}. Checked:\n{rendered}")


def introspect_sqlite_schema(path: str | Path) -> dict[str, tuple[str, ...]]:
    database_path = Path(path)
    uri = f"file:{database_path.resolve().as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        table_rows = connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        schema: dict[str, tuple[str, ...]] = {}
        for (table_name,) in table_rows:
            quoted = table_name.replace('"', '""')
            columns = connection.execute(f'PRAGMA table_info("{quoted}")').fetchall()
            schema[table_name] = tuple(str(column[1]) for column in columns)
        return schema


def render_schema(schema: dict[str, tuple[str, ...]]) -> str:
    return "\n".join(
        f"{table}({', '.join(columns)})" for table, columns in sorted(schema.items())
    )


def _load_payload(path: Path) -> Iterable[dict[str, Any]]:
    if path.suffix.lower() == ".jsonl":
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    payload = json.loads(path.read_text())
    if not isinstance(payload, list):
        raise ValueError("BIRD dataset must be a JSON array or JSONL file")
    return payload


def _required_string(item: dict[str, Any], key: str, index: int) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Example {index} is missing {key!r}")
    return value.strip()
