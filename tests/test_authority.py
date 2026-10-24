from __future__ import annotations

import sqlite3

import pytest

from sqlharness.authority import DatabaseAuthority


@pytest.fixture
def authority(tmp_path):
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
    return DatabaseAuthority(tmp_path, max_rows=10)


def test_authority_executes_validated_read_only_query(authority):
    receipt = authority.execute("shop", "SELECT name FROM customers ORDER BY id")
    assert receipt["decision"] == "executed"
    assert receipt["execution"]["rows"] == (("Ada",), ("Grace",))
    assert receipt["receipt_id"]
    assert "immutability" in receipt["receipt_semantics"]


def test_authority_blocks_mutation_before_touching_database(authority):
    receipt = authority.execute("shop", "DELETE FROM customers")
    assert receipt["decision"] == "blocked"
    assert receipt["database_touched"] is False
    assert receipt["execution"] is None
    assert "non_readonly_operations" in receipt["validation"]["errors"][0]
    assert authority.execute("shop", "SELECT COUNT(*) FROM customers")["execution"]["rows"] == ((2,),)


def test_authority_rejects_database_path_traversal(authority):
    with pytest.raises(ValueError, match="Invalid database ID"):
        authority.schema("../shop")


def test_authority_exposes_bounded_query_plan(authority):
    receipt = authority.explain("shop", "SELECT name FROM customers")
    assert receipt["decision"] == "explained"
    assert receipt["query_plan"]["succeeded"] is True
