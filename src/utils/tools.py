
from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
import polars as pl

class DateParser:
    """Convert common date values to datetime.date."""

    SUPPORTED_FORMATS = ("%Y%m%d", "%Y-%m-%d", "%Y/%m/%d")

    @classmethod
    def to_date(cls, value: int | str | date | datetime) -> date:
        if isinstance(value, datetime):
            return value.date()

        if isinstance(value, date):
            return value

        if isinstance(value, int):
            return cls._parse_str(str(value))

        if isinstance(value, str):
            return cls._parse_str(value)

        raise TypeError(f"Unsupported date value type: {type(value).__name__}")

    @classmethod
    def _parse_str(cls, value: str) -> date:
        text = value.strip()
        if not text:
            raise ValueError("Date string cannot be empty.")
        if text.lower() == "today":
            return date.today()

        for fmt in cls.SUPPORTED_FORMATS:
            try:
                return datetime.strptime(text, fmt).date()
            except ValueError:
                continue

        raise ValueError(
            f"Unsupported date format: {value!r}. "
            "Expected today, YYYYMMDD, YYYY-MM-DD, or YYYY/MM/DD."
        )


class DateFormatter:
    """Convert date-like values to int or string date formats."""

    @staticmethod
    def to_int(value: int | str | date | datetime) -> int:
        parsed = DateParser.to_date(value)
        return int(parsed.strftime("%Y%m%d"))

    @staticmethod
    def to_str(
        value: int | str | date | datetime,
        fmt: str = "%Y%m%d",
    ) -> str:
        parsed = DateParser.to_date(value)
        return parsed.strftime(fmt)

    @staticmethod
    def to_dash_str(value: int | str | date | datetime) -> str:
        return DateFormatter.to_str(value, "%Y-%m-%d")


class DatePolars:
    """Polars expression helpers that mirror DateParser's supported formats."""

    @staticmethod
    def to_date_expr(expr: str | pl.Expr) -> pl.Expr:
        source = pl.col(expr) if isinstance(expr, str) else expr
        as_text = source.cast(pl.Utf8)
        return (
            pl.when(source.is_null())
            .then(None)
            .when(as_text.str.len_chars() == 8)
            .then(as_text.str.strptime(pl.Date, "%Y%m%d", strict=False))
            .when(as_text.str.contains("-"))
            .then(as_text.str.strptime(pl.Date, "%Y-%m-%d", strict=False))
            .otherwise(as_text.str.strptime(pl.Date, "%Y/%m/%d", strict=False))
        )

    @staticmethod
    def to_datetime_expr(expr: str | pl.Expr) -> pl.Expr:
        source = pl.col(expr) if isinstance(expr, str) else expr
        return source.cast(pl.Datetime, strict=False)



class UpdateLocalParquet:
    """Incrementally merge tabular data into one local parquet file."""

    @staticmethod
    def _to_polars(df: pl.DataFrame | pd.DataFrame) -> pl.DataFrame:
        if isinstance(df, pl.DataFrame):
            return df
        if isinstance(df, pd.DataFrame):
            return pl.from_pandas(df)
        raise TypeError(f"Unsupported dataframe type: {type(df).__name__}")

    @staticmethod
    def _apply_filters(
        lazy_frame: pl.LazyFrame,
        filters: dict[str, Any] | None = None,
    ) -> pl.LazyFrame:
        if not filters:
            return lazy_frame

        for column, value in filters.items():
            lazy_frame = lazy_frame.filter(pl.col(column) == value)
        return lazy_frame

    @staticmethod
    def get_last_update_date(
        file: Path,
        date_col: str = "trade_date",
        filters: dict[str, Any] | None = None,
    ) -> date | None:
        """
        获取数据库文件中的最大交易日期。

        :param file: Parquet 文件路径
        :return: 最大日期或 None
        """
        if file.exists():
            result = (
                UpdateLocalParquet._apply_filters(pl.scan_parquet(file), filters)
                .select(pl.col(date_col).max().alias(date_col))
                .collect()
            )
            value = result.item(0, date_col)
            return DateParser.to_date(value) if value is not None else None
        return None

    @staticmethod
    def get_first_update_date(
        file: Path,
        date_col: str = "trade_date",
        filters: dict[str, Any] | None = None,
    ) -> date | None:
        """
        获取数据库文件中的最早交易日期。

        :param file: Parquet 文件路径
        :return: 最大日期或 None
        """
        if file.exists():
            result = (
                UpdateLocalParquet._apply_filters(pl.scan_parquet(file), filters)
                .select(pl.col(date_col).min().alias(date_col))
                .collect()
            )
            value = result.item(0, date_col)
            return DateParser.to_date(value) if value is not None else None
        return None

    @staticmethod
    def get_instrument_id(file: Path) -> list[str]:
        """获取数据库中当前的所有instrument_id"""
        if file.exists():
            df = pl.read_parquet(file)
            return df["instrument_id"].unique().sort().to_list()
        return []

    @staticmethod
    def update(
        file: Path,
        df: pl.DataFrame | pd.DataFrame,
        unique_subset: list[str] | None = None,
        sort_by: list[str] | None = None,
    ) -> int:
        """
        合并新数据并写入本地数据库。

        :param file: Parquet 文件路径
        :param df: 新数据，需包含 trade_date 和 instrument_id
        :param unique_subset: 用于去重的列名列表
        """
        unique_subset = unique_subset or ["trade_date", "instrument_id"]
        sort_by = sort_by or unique_subset
        file.parent.mkdir(parents=True, exist_ok=True)
        incoming = UpdateLocalParquet._to_polars(df)
        if incoming.is_empty():
            return UpdateLocalParquet.get_max_rows(file)

        incoming = incoming.with_columns(pl.lit(1).alias("_qdb_update_priority"))

        if file.exists():
            old = pl.read_parquet(file).with_columns(
                pl.lit(0).alias("_qdb_update_priority")
            )
            merged = pl.concat([old, incoming], how="diagonal_relaxed")
        else:
            merged = incoming

        merged = (
            merged.unique(subset=unique_subset, keep="last", maintain_order=True)
            .drop("_qdb_update_priority")
            .sort(sort_by)
        )
        merged.write_parquet(file)
        return merged.height

    update_old = update

    @staticmethod
    def get_max_rows(file: Path) -> int:
        if file.exists():
            df = pl.read_parquet(file)
            return df.shape[0]
        return 0

    @staticmethod
    def get_update_range(
        file: Path,
        start: int | str | date | datetime,
        end: int | str | date | datetime,
        list_date: int | str | date | datetime | None = None,
        delist_date: int | str | date | datetime | None = None,
        date_col: str = "trade_date",
        filters: dict[str, Any] | None = None,
    ) -> list[tuple[date, date]]:
        start = DateParser.to_date(start)
        end = DateParser.to_date(end)
        list_date = DateParser.to_date(list_date) if list_date is not None else None
        delist_date = (
            DateParser.to_date(delist_date) if delist_date is not None else None
        )
        
        if start > end:
            raise ValueError(f"起始日期 {start} 不能晚于结束日期 {end}")

        # 上市日前不更新，退市日后不更新
        if list_date is not None:
            start = max(start, list_date)
        if delist_date is not None:
            end = min(end, delist_date)
        if start > end:
            return []

        if not file.exists():
            return [(start, end)]

        local_first = UpdateLocalParquet.get_first_update_date(
            file, date_col=date_col, filters=filters
        )
        local_last = UpdateLocalParquet.get_last_update_date(
            file, date_col=date_col, filters=filters
        )

        if local_first is None or local_last is None:
            return [(start, end)]

        # 如果已经完全覆盖
        if local_first <= start and local_last >= end:
            return []

        update_ranges = []

        # 请求起始 < 本地起始 → 补充 [start, local_first - 1 day]
        if start < local_first:
            update_ranges.append((start, local_first - timedelta(days=1)))

        # 请求结束 > 本地结束 → 补充 [local_last + 1 day, end]
        if end > local_last:
            update_ranges.append((local_last + timedelta(days=1), end))

        update_ranges = [(s, e) for s, e in update_ranges if s <= e]

        return update_ranges


UpdateLocalDB = UpdateLocalParquet


class DuckDBTableState:
    """Read update state from canonical DuckDB tables."""

    @staticmethod
    def _where_clause(filters: dict[str, Any] | None = None) -> tuple[str, list[Any]]:
        if not filters:
            return "", []

        clauses = []
        values = []
        for column, value in filters.items():
            clauses.append(f"{column} = ?")
            values.append(value)
        return " WHERE " + " AND ".join(clauses), values

    @staticmethod
    def get_last_update_date(
        con: duckdb.DuckDBPyConnection,
        table_name: str,
        date_col: str = "trade_date",
        filters: dict[str, Any] | None = None,
    ) -> date | None:
        where_sql, values = DuckDBTableState._where_clause(filters)
        try:
            row = con.execute(
                f"SELECT max({date_col}) FROM {table_name}{where_sql};",
                values,
            ).fetchone()
        except duckdb.CatalogException:
            return None

        return DateParser.to_date(row[0]) if row and row[0] is not None else None

    @staticmethod
    def get_first_update_date(
        con: duckdb.DuckDBPyConnection,
        table_name: str,
        date_col: str = "trade_date",
        filters: dict[str, Any] | None = None,
    ) -> date | None:
        where_sql, values = DuckDBTableState._where_clause(filters)
        try:
            row = con.execute(
                f"SELECT min({date_col}) FROM {table_name}{where_sql};",
                values,
            ).fetchone()
        except duckdb.CatalogException:
            return None

        return DateParser.to_date(row[0]) if row and row[0] is not None else None

    @staticmethod
    def get_update_range(
        con: duckdb.DuckDBPyConnection,
        table_name: str,
        start: int | str | date | datetime,
        end: int | str | date | datetime,
        date_col: str = "trade_date",
        filters: dict[str, Any] | None = None,
        list_date: int | str | date | datetime | None = None,
        delist_date: int | str | date | datetime | None = None,
    ) -> list[tuple[date, date]]:
        start = DateParser.to_date(start)
        end = DateParser.to_date(end)
        list_date = DateParser.to_date(list_date) if list_date is not None else None
        delist_date = (
            DateParser.to_date(delist_date) if delist_date is not None else None
        )

        if start > end:
            raise ValueError(f"起始日期 {start} 不能晚于结束日期 {end}")

        if list_date is not None:
            start = max(start, list_date)
        if delist_date is not None:
            end = min(end, delist_date)
        if start > end:
            return []

        local_first = DuckDBTableState.get_first_update_date(
            con,
            table_name,
            date_col=date_col,
            filters=filters,
        )
        local_last = DuckDBTableState.get_last_update_date(
            con,
            table_name,
            date_col=date_col,
            filters=filters,
        )

        if local_first is None or local_last is None:
            return [(start, end)]

        if local_first <= start and local_last >= end:
            return []

        update_ranges = []
        if start < local_first:
            update_ranges.append((start, local_first - timedelta(days=1)))
        if end > local_last:
            update_ranges.append((local_last + timedelta(days=1), end))

        return [(s, e) for s, e in update_ranges if s <= e]




if __name__ == "__main__":
    print(DateFormatter.to_dash_str(date(2026, 5, 27)))
    print(DateParser.to_date("2026-05-27"))
