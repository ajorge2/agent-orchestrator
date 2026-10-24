from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Annotated, Any

from pydantic import Field

from .authority import DatabaseAuthority


DEFAULT_DATABASE_ROOT = Path("data/bird/minidev/MINIDEV/dev_databases")
DatabaseId = Annotated[str, Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")]
SQL = Annotated[str, Field(min_length=1, max_length=16000)]
PRIVACY = {
    "scope": "SQLHarness MCP entry point only",
    "content_storage": "No tool inputs or outputs saved by this server",
    "external_api_calls": False,
    "telemetry": False,
    "local_data": "Explicit database root; files opened read-only",
    "demo": "Synthetic shop database; temporary sample files removed on normal exit",
    "host_notice": "Your AI application may retain results or send them to its model provider",
}


def _bounded(operation, *args) -> dict[str, Any]:
    from mcp.server.mcpserver.exceptions import ToolError
    try:
        result = operation(*args)
        if len(json.dumps(result, default=str).encode()) > 512_000:
            raise ToolError("Result too large. Select fewer columns or add a smaller LIMIT.")
        return result
    except ToolError:
        raise
    except Exception:
        raise ToolError("Database request failed. Check the database ID and query.") from None


def create_server(
    database_root: str | Path,
    *,
    timeout_seconds: float = 5.0,
    max_rows: int = 1_000,
) -> Any:
    try:
        from mcp.server import MCPServer
    except ImportError as error:
        raise RuntimeError("Install the MCP extra: uv sync --extra mcp") from error

    from mcp.types import ToolAnnotations
    if not 0 < timeout_seconds <= 30 or not 1 <= max_rows <= 1000:
        raise ValueError("Timeout must be in (0, 30]; row limit must be in [1, 1000]")
    authority = DatabaseAuthority(
        database_root,
        timeout_seconds=timeout_seconds,
        max_rows=max_rows,
    )
    server = MCPServer(
        "SQLHarness Database Authority",
        version="0.2.0", log_level="CRITICAL",
        instructions=(
            "Inspect schemas, validate proposed SQL, and execute only single-statement "
            "read-only SQLite queries through bounded SQLHarness policy checks. "
            "No request content is persisted or sent to external APIs by this entry point. "
            "Your AI host has its own retention policy. Read privacy://policy for details."
        ),
    )

    readonly = ToolAnnotations(read_only_hint=True, destructive_hint=False,
                               idempotent_hint=True, open_world_hint=False)

    @server.resource("privacy://policy")
    def privacy_policy() -> str:
        return json.dumps(PRIVACY)

    @server.tool(annotations=readonly)
    def list_databases() -> dict[str, Any]:
        """List databases governed by this server."""

        return _bounded(authority.list_databases)

    @server.tool(annotations=readonly)
    def inspect_schema(db_id: DatabaseId) -> dict[str, Any]:
        """Return tables and columns for one governed database."""

        return _bounded(authority.schema, db_id)

    @server.tool(annotations=readonly)
    def validate_sql(db_id: DatabaseId, sql: SQL) -> dict[str, Any]:
        """Parse SQL and check schema references and read-only policy without execution."""

        return _bounded(authority.validate, db_id, sql)

    @server.tool(annotations=readonly)
    def explain_sql(db_id: DatabaseId, sql: SQL) -> dict[str, Any]:
        """Validate a read-only query and return SQLite's bounded query plan."""

        return _bounded(authority.explain, db_id, sql)

    @server.tool(annotations=readonly)
    def execute_readonly_sql(db_id: DatabaseId, sql: SQL) -> dict[str, Any]:
        """Validate and execute one read-only query with time and row limits."""

        return _bounded(authority.execute, db_id, sql)

    return server


def create_demo_database(root: Path) -> Path:
    """Materialize only synthetic sample data, never request content."""
    directory = root / "shop"
    directory.mkdir()
    with sqlite3.connect(directory / "shop.sqlite") as connection:
        connection.executescript("""
            CREATE TABLE customers (id INTEGER PRIMARY KEY, segment TEXT);
            CREATE TABLE orders (id INTEGER PRIMARY KEY, customer_id INTEGER,
                                 amount_usd REAL, status TEXT);
            INSERT INTO customers VALUES (1, 'Startup'), (2, 'Enterprise'), (3, 'Startup');
            INSERT INTO orders VALUES (1, 1, 120, 'paid'), (2, 2, 900, 'paid'),
                                      (3, 3, 80, 'pending'), (4, 1, 200, 'paid');
        """)
    return root


def main() -> None:
    parser = argparse.ArgumentParser(description="SQLHarness MCP: synthetic demo by default")
    parser.add_argument("--database-root", type=Path, help="Explicit local SQLite root (stdio only)")
    parser.add_argument("--http-demo", action="store_true", help="Synthetic data on loopback /mcp")
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()
    root = args.database_root or (Path(os.environ["SQLHARNESS_DATABASE_ROOT"])
                                 if os.environ.get("SQLHARNESS_DATABASE_ROOT") else None)
    if args.http_demo and root is not None:
        parser.error("HTTP demo cannot be combined with a personal database root")
    if root is not None and not root.is_dir():
        parser.error("The database root must be an existing directory")
    if not 1 <= args.port <= 65535:
        parser.error("Port must be between 1 and 65535")
    logging.disable(logging.CRITICAL)
    with tempfile.TemporaryDirectory(prefix="sqlharness-synthetic-") as temporary:
        database_root = root if root is not None else create_demo_database(Path(temporary))
        server = create_server(database_root)
        if args.http_demo:
            server.run(transport="streamable-http", host="127.0.0.1", port=args.port,
                       stateless_http=True, json_response=True, max_request_body_size=64000)
        else:
            server.run()


if __name__ == "__main__":
    main()
