from __future__ import annotations

from dataclasses import fields
from typing import get_type_hints

import duckdb

from quant_database.schema import SCHEMAS, TableSchema
from quant_database.schema.base import duckdb_type


def ensure_table(con: duckdb.DuckDBPyConnection, schema: TableSchema) -> None:
    con.execute(schema.create_table_sql())
    sync_missing_columns(con, schema)
    for sql in schema.create_index_sqls():
        con.execute(sql)


def ensure_all_tables(con: duckdb.DuckDBPyConnection) -> None:
    for schema in SCHEMAS:
        ensure_table(con, schema)


def sync_missing_columns(
    con: duckdb.DuckDBPyConnection,
    schema: TableSchema,
) -> None:
    existing_columns = {
        row[0]
        for row in con.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = ?
            """,
            [schema.name],
        ).fetchall()
    }
    type_hints = get_type_hints(schema.model)

    for field in fields(schema.model):
        if field.name in existing_columns:
            continue
        column_type = duckdb_type(type_hints[field.name])
        con.execute(f"ALTER TABLE {schema.name} ADD COLUMN {field.name} {column_type}")
