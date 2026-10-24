from __future__ import annotations

from collections.abc import Mapping

from .types import ValidationResult


def validate_readonly_sql(
    sql: str,
    schema: Mapping[str, tuple[str, ...]],
    *,
    dialect: str = "sqlite",
) -> ValidationResult:
    try:
        import sqlglot
        from sqlglot import exp
    except ImportError as error:
        raise RuntimeError("SQL validation requires the sqlglot dependency") from error

    errors: list[str] = []
    try:
        statements = sqlglot.parse(sql, read=dialect)
    except sqlglot.errors.ParseError as error:
        return ValidationResult(valid=False, normalized_sql=None, errors=(f"parse_error: {error}",))

    if len(statements) != 1:
        return ValidationResult(
            valid=False,
            normalized_sql=None,
            errors=(f"expected_one_statement: found {len(statements)}",),
        )

    expression = statements[0]
    blocked_types = tuple(
        expression_type
        for name in ("Alter", "Command", "Create", "Delete", "Drop", "Insert", "Merge", "Transaction", "Update")
        if (expression_type := getattr(exp, name, None)) is not None
    )
    blocked = sorted({node.key.upper() for node in expression.walk() if isinstance(node, blocked_types)})
    if blocked:
        errors.append(f"non_readonly_operations: {', '.join(blocked)}")
    elif not isinstance(expression, exp.Query):
        # SQLite's query_only pragma does not make administrative statements
        # such as ATTACH an acceptable part of a governed query interface.
        errors.append("expected_readonly_query")

    schema_by_lower = {table.lower(): {column.lower() for column in columns} for table, columns in schema.items()}
    cte_names = {
        cte.alias_or_name.lower()
        for cte in expression.find_all(exp.CTE)
        if cte.alias_or_name
    }
    alias_to_table: dict[str, str] = {}
    referenced_tables: set[str] = set()
    for table in expression.find_all(exp.Table):
        table_name = table.name
        if not table_name:
            continue
        table_key = table_name.lower()
        if table_key in cte_names:
            continue
        referenced_tables.add(table_name)
        if table_key not in schema_by_lower:
            errors.append(f"unknown_table: {table_name}")
            continue
        alias_to_table[(table.alias_or_name or table_name).lower()] = table_key

    referenced_columns: set[str] = set()
    all_columns = set().union(*schema_by_lower.values()) if schema_by_lower else set()
    output_aliases = {
        selection.alias.lower()
        for selection in getattr(expression, "selects", ())
        if selection.alias
    }
    for column in expression.find_all(exp.Column):
        column_name = column.name
        if not column_name or column_name == "*":
            continue
        referenced_columns.add(column.sql(dialect=dialect))
        column_key = column_name.lower()
        qualifier = column.table.lower() if column.table else ""
        if qualifier:
            if qualifier in cte_names:
                continue
            physical_table = alias_to_table.get(qualifier, qualifier)
            known_columns = schema_by_lower.get(physical_table)
            if known_columns is not None and column_key not in known_columns:
                errors.append(f"unknown_column: {column.table}.{column_name}")
        elif column_key not in all_columns and column_key not in output_aliases:
            errors.append(f"unknown_column: {column_name}")

    normalized = expression.sql(dialect=dialect, pretty=False)
    return ValidationResult(
        valid=not errors,
        normalized_sql=normalized,
        errors=tuple(dict.fromkeys(errors)),
        referenced_tables=tuple(sorted(referenced_tables)),
        referenced_columns=tuple(sorted(referenced_columns)),
    )
