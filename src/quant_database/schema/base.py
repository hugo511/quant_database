from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import date, datetime
from types import UnionType
from typing import Any, Union, get_args, get_origin, get_type_hints

import polars as pl
from utils.tools import DatePolars


def _unwrap_optional(annotation: Any) -> tuple[Any, bool]:
    origin = get_origin(annotation)
    if origin in (Union, UnionType):
        args = [arg for arg in get_args(annotation) if arg is not type(None)]
        if len(args) == 1:
            return args[0], True
    return annotation, False


def duckdb_type(annotation: Any) -> str:
    annotation, _ = _unwrap_optional(annotation)
    type_map = {
        str: "VARCHAR",
        int: "BIGINT",
        float: "DOUBLE",
        bool: "BOOLEAN",
        date: "DATE",
        datetime: "TIMESTAMP",
    }
    if annotation not in type_map:
        raise TypeError(f"Unsupported schema type: {annotation!r}")
    return type_map[annotation]


def polars_type(annotation: Any) -> pl.DataType:
    annotation, _ = _unwrap_optional(annotation)
    type_map = {
        str: pl.Utf8,
        int: pl.Int64,
        float: pl.Float64,
        bool: pl.Boolean,
        date: pl.Date,
        datetime: pl.Datetime,
    }
    if annotation not in type_map:
        raise TypeError(f"Unsupported schema type: {annotation!r}")
    return type_map[annotation]


class SchemaModel:
    @classmethod
    def column_names(cls) -> list[str]:
        return [field.name for field in fields(cls)]

    @classmethod
    def format_polars(cls, df: pl.DataFrame) -> pl.DataFrame:
        type_hints = get_type_hints(cls)
        formatted = df

        for field in fields(cls):
            annotation = type_hints[field.name]
            _, optional = _unwrap_optional(annotation)
            dtype = polars_type(annotation)

            if field.name not in formatted.columns:
                if optional:
                    formatted = formatted.with_columns(
                        pl.lit(None, dtype=dtype).alias(field.name)
                    )
                    continue
                raise ValueError(f"{cls.__name__} missing required column: {field.name}")

            formatted = formatted.with_columns(
                _cast_expr(field.name, dtype).alias(field.name)
            )

        return formatted.select(cls.column_names())


def _cast_expr(column: str, dtype: pl.DataType) -> pl.Expr:
    expr = pl.col(column)
    if dtype == pl.Date:
        return DatePolars.to_date_expr(expr)
    if dtype == pl.Datetime:
        return DatePolars.to_datetime_expr(expr)
    return expr.cast(dtype, strict=False)


@dataclass(frozen=True)
class TableSchema:
    name: str
    model: type
    primary_key: tuple[str, ...]
    description: str
    indexes: tuple[tuple[str, ...], ...] = ()

    @property
    def column_names(self) -> list[str]:
        return [field.name for field in fields(self.model)]

    def create_table_sql(self) -> str:
        type_hints = get_type_hints(self.model)
        columns: list[str] = []

        for field in fields(self.model):
            annotation = type_hints[field.name]
            _, optional = _unwrap_optional(annotation)
            null_sql = "" if optional else " NOT NULL"
            columns.append(f"{field.name} {duckdb_type(annotation)}{null_sql}")

        if self.primary_key:
            columns.append(f"PRIMARY KEY ({', '.join(self.primary_key)})")

        column_sql = ",\n    ".join(columns)
        return f"CREATE TABLE IF NOT EXISTS {self.name} (\n    {column_sql}\n);"

    def create_index_sqls(self) -> list[str]:
        statements = []
        for index_columns in self.indexes:
            index_name = f"idx_{self.name}_{'_'.join(index_columns)}"
            columns = ", ".join(index_columns)
            statements.append(
                f"CREATE INDEX IF NOT EXISTS {index_name} ON {self.name} ({columns});"
            )
        return statements


if __name__ == "__main__":
    @dataclass
    class Test:
        id: int
        name: str
        age: int

    print(TableSchema(name="test", model=Test, primary_key=("id",), description="test").create_table_sql())
    print(TableSchema(name="test", model=Test, primary_key=("id",), description="test").create_index_sqls())
