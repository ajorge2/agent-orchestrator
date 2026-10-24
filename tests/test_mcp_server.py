from __future__ import annotations

import asyncio
import sqlite3

from mcp import Client

from sqlharness.mcp_server import create_server


def test_mcp_server_enforces_authority_boundary(tmp_path):
    database_dir = tmp_path / "shop"
    database_dir.mkdir()
    database_path = database_dir / "shop.sqlite"
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT);
            INSERT INTO customers VALUES (1, 'Ada'), (2, 'Grace');
            """
        )

    async def exercise_server():
        server = create_server(tmp_path, max_rows=10)
        async with Client(server, raise_exceptions=True) as client:
            tools = await client.list_tools()
            blocked = await client.call_tool(
                "execute_readonly_sql",
                {"db_id": "shop", "sql": "DELETE FROM customers"},
            )
            executed = await client.call_tool(
                "execute_readonly_sql",
                {
                    "db_id": "shop",
                    "sql": "SELECT name FROM customers ORDER BY id",
                },
            )
            return tools, blocked, executed

    tools, blocked, executed = asyncio.run(exercise_server())
    assert {tool.name for tool in tools.tools} == {
        "execute_readonly_sql",
        "explain_sql",
        "inspect_schema",
        "list_databases",
        "validate_sql",
    }
    assert blocked.structured_content["decision"] == "blocked"
    assert blocked.structured_content["database_touched"] is False
    assert executed.structured_content["decision"] == "executed"
    assert executed.structured_content["execution"]["rows"] == [["Ada"], ["Grace"]]
