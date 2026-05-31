from __future__ import annotations

from uuid import uuid4

import duckdb
import pandas as pd
import polars as pl

from quant_database.schema.base import TableSchema


def upsert_frame(
    con: duckdb.DuckDBPyConnection,
    schema: TableSchema,
    frame: pl.DataFrame | pd.DataFrame,
) -> int:
    if len(frame) == 0:
        return 0

    if isinstance(frame, pl.DataFrame):
        missing = set(schema.column_names) - set(frame.columns)
        if missing:
            raise ValueError(f"{schema.name} missing columns: {sorted(missing)}")
        incoming = frame.select(schema.column_names)
    else:
        missing = set(schema.column_names) - set(frame.columns)
        if missing:
            raise ValueError(f"{schema.name} missing columns: {sorted(missing)}")
        incoming = frame.loc[:, schema.column_names].copy()

    temp_name = f"_incoming_{schema.name}_{uuid4().hex}"
    try:
        con.register(temp_name, incoming)
        pk_match = " AND ".join(
            f"target.{column} = incoming.{column}" for column in schema.primary_key
        )
        columns = ", ".join(schema.column_names)
        con.execute(
            f"""
            DELETE FROM {schema.name} AS target
            WHERE EXISTS (
                SELECT 1 FROM {temp_name} AS incoming
                WHERE {pk_match}
            );
            """
        )
        con.execute(
            f"""
            INSERT INTO {schema.name} ({columns})
            SELECT {columns}
            FROM {temp_name};
            """
        )
    finally:
        try:
            con.unregister(temp_name)
        except duckdb.InvalidInputException:
            pass

    return len(frame)


upsert_dataframe = upsert_frame
