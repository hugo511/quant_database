from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import polars as pl

from quant_database.core.bootstrap import ensure_all_tables
from quant_database.core.connection import connect
from quant_database.core.upsert import upsert_frame
from quant_database.providers.tushare_client import TushareClient, find_project_root
from quant_database.schema.base import TableSchema
from quant_database.schema.market import MARKET_BARS_DAILY, MARKET_BARS_DERIVATIVE_DAILY, MARKET_FX_DAILY
from quant_database.schema.reference import INSTRUMENT_STOCK_ST, REFERENCE_INSTRUMENT, REFERENCE_FUTURE
from utils.logger import logger
from utils.tools import DateParser, DuckDBTableState, UpdateLocalParquet


@dataclass(frozen=True)
class LoadResult:
    dataset: str
    raw_path: Path
    db_path: Path
    raw_rows: int
    rows_written: int


def data_dir(root_dir: str | Path | None = None) -> Path:
    if root_dir is None:
        return find_project_root() / "data"
    return Path(root_dir).expanduser().resolve()


def default_db_path(root_dir: str | Path | None = None) -> Path:
    return data_dir(root_dir) / "quant.duckdb"


def raw_parquet_path(
    table_name: str,
    source_id: str = "tushare",
    root_dir: str | Path | None = None,
) -> Path:
    return data_dir(root_dir) / "raw" / source_id / f"{table_name}.parquet"


def pandas_to_polars(df: pd.DataFrame) -> pl.DataFrame:
    return pl.from_pandas(df)


class BaseTushareDataset:
    name: str
    table: TableSchema
    raw_unique_subset: list[str]
    raw_sort_by: list[str]
    raw_name: str | None = None
    raw_date_col: str | None = None
    db_date_col: str | None = None
    incremental: bool = False

    def __init__(
        self,
        client: TushareClient,
        root_dir: str | Path,
        db_path: str | Path,
    ) -> None:
        self.client = client
        self.root_dir = Path(root_dir).expanduser().resolve()
        self.db_path = Path(db_path).expanduser().resolve()

    @property
    def raw_path(self) -> Path:
        return raw_parquet_path(self.raw_name or self.table.name, root_dir=self.root_dir)

    def update(self, **params: Any) -> LoadResult:
        raw_update = self.fetch_incremental(params)
        if raw_update.is_empty():
            return LoadResult(
                dataset=self.name,
                raw_path=self.raw_path,
                db_path=self.db_path,
                raw_rows=0,
                rows_written=0,
            )

        if not raw_update.is_empty():
            UpdateLocalParquet.update(
                self.raw_path,
                raw_update,
                unique_subset=self.raw_unique_subset,
                sort_by=self.raw_sort_by,
            )

        schema_frame = self.to_schema_frame(raw_update, params)
        formatted = self.table.model.format_polars(schema_frame)

        with connect(self.db_path) as con:
            ensure_all_tables(con)
            rows_written = upsert_frame(con, self.table, formatted)

        return LoadResult(
            dataset=self.name,
            raw_path=self.raw_path,
            db_path=self.db_path,
            raw_rows=raw_update.height,
            rows_written=rows_written,
        )

    def fetch_incremental(self, params: dict[str, Any]) -> pl.DataFrame:
        if not self.incremental:
            return self.fetch_raw(params)

        start = DateParser.to_date(params["start"])
        end = DateParser.to_date(params["end"])
        with connect(self.db_path) as con:
            ensure_all_tables(con)
            ranges = DuckDBTableState.get_update_range(
                con,
                self.table.name,
                start,
                end,
                date_col=self.db_date_col or self.raw_date_col or "trade_date",
                filters=self.db_update_filters(params),
            )

        frames: list[pl.DataFrame] = []
        for range_start, range_end in ranges:
            range_params = dict(params)
            range_params["start"] = range_start
            range_params["end"] = range_end
            fetched = self.fetch_raw(range_params)
            if not fetched.is_empty():
                frames.append(fetched)

        if not frames:
            return pl.DataFrame()
        return pl.concat(frames, how="diagonal_relaxed")

    def fetch_raw(self, params: dict[str, Any]) -> pl.DataFrame:
        raise NotImplementedError

    def to_schema_frame(self, raw: pl.DataFrame, params: dict[str, Any]) -> pl.DataFrame:
        raise NotImplementedError

    def db_update_filters(self, params: dict[str, Any]) -> dict[str, Any] | None:
        return None

    def get_update_ranges_by_instrument(
        self,
        instrument_ids: list[str],
        start: date,
        end: date,
        table: TableSchema | None = None,
    ) -> dict[str, list[tuple[date, date]]]:
        if not instrument_ids:
            return {}

        target_table = table or self.table
        date_col = self.db_date_col or self.raw_date_col or "trade_date"
        placeholders = ", ".join(["?"] * len(instrument_ids))

        with connect(self.db_path) as con:
            ensure_all_tables(con)
            rows = con.execute(
                f"""
                SELECT instrument_id,
                       min({date_col}) AS first_trade_date,
                       max({date_col}) AS last_trade_date
                FROM {target_table.name}
                WHERE source_id = ?
                  AND instrument_id IN ({placeholders})
                GROUP BY instrument_id
                """,
                ["tushare", *instrument_ids],
            ).fetchall()

        state = {
            instrument_id: (
                DateParser.to_date(first_trade_date),
                DateParser.to_date(last_trade_date),
            )
            for instrument_id, first_trade_date, last_trade_date in rows
            if first_trade_date is not None and last_trade_date is not None
        }

        ranges_by_instrument: dict[str, list[tuple[date, date]]] = {}
        for instrument_id in instrument_ids:
            local_state = state.get(instrument_id)
            if local_state is None:
                ranges_by_instrument[instrument_id] = [(start, end)]
                continue

            local_first, local_last = local_state
            ranges: list[tuple[date, date]] = []
            if start < local_first:
                ranges.append((start, local_first - timedelta(days=1)))
            if end > local_last:
                ranges.append((local_last + timedelta(days=1), end))
            ranges_by_instrument[instrument_id] = [
                (range_start, range_end)
                for range_start, range_end in ranges
                if range_start <= range_end
            ]

        return ranges_by_instrument

    def ensure_schema_columns(
        self,
        df: pl.DataFrame,
        table: TableSchema | None = None,
    ) -> pl.DataFrame:
        target_table = table or self.table
        for column in target_table.column_names:
            if column not in df.columns:
                df = df.with_columns(pl.lit(None).alias(column))
        return df


class InstrumentStockDataset(BaseTushareDataset):
    name = "stock_list"
    table = REFERENCE_INSTRUMENT
    raw_unique_subset = ["ts_code"]
    raw_sort_by = ["ts_code"]

    def fetch_raw(self, params: dict[str, Any]) -> pl.DataFrame:
        return pandas_to_polars(self.client.get_stock_basic(fields=params.get("fields")))

    def to_schema_frame(self, raw: pl.DataFrame, params: dict[str, Any]) -> pl.DataFrame:
        if raw.is_empty():
            return raw

        df = raw.with_columns(
            [
                pl.col("ts_code").alias("instrument_id"),
                pl.lit("equity").alias("asset_class"),
                pl.lit("stock").alias("instrument_type"),
                pl.col("ts_code").alias("source_code"),
                pl.lit("tushare").alias("source_id"),
                pl.col("fullname").alias("full_name"),
                pl.col("curr_type").alias("currency"),
                (pl.col("is_hs").fill_null("N") != "N").alias("is_hs"),
            ]
        )
        return self.ensure_schema_columns(df)


class InstrumentStockStDataset(BaseTushareDataset):
    name = "stock_st"
    table = INSTRUMENT_STOCK_ST
    raw_unique_subset = ["ts_code", "trade_date"]
    raw_sort_by = ["trade_date", "ts_code"]

    def fetch_raw(self, params: dict[str, Any]) -> pl.DataFrame:
        return pandas_to_polars(
            self.client.get_stock_st(start=params["start"], end=params["end"], fields=params.get("fields"))
        )
    
    def to_schema_frame(self, raw: pl.DataFrame, params: dict[str, Any]) -> pl.DataFrame:
        if raw.is_empty():
            return raw

        df = raw.with_columns(
            [
                pl.col("ts_code").alias("instrument_id"),
                pl.col("ts_code").alias("source_code"),
                pl.lit("tushare").alias("source_id"),
            ]
        )
        return self.ensure_schema_columns(df)


class MarketBarsStockDailyDataset(BaseTushareDataset):
    name = "stock_daily"
    table = MARKET_BARS_DAILY
    raw_name = "stock_daily"
    raw_unique_subset = ["ts_code", "trade_date"]
    raw_sort_by = ["ts_code", "trade_date"]
    raw_date_col = "trade_date"
    db_date_col = "trade_date"
    incremental = True

    def fetch_raw(self, params: dict[str, Any]) -> pl.DataFrame:
        start = DateParser.to_date(params["start"])
        end = DateParser.to_date(params["end"])
        frames: list[pl.DataFrame] = []
        current = start
        while current <= end:
            raw = self.client.get_stock_daily(
                trade_date=current,
                fields=params.get("fields"),
            )
            if not raw.empty:
                frames.append(pandas_to_polars(raw))
            current = current + timedelta(days=1)

        if not frames:
            return pl.DataFrame()
        return pl.concat(frames, how="diagonal_relaxed")

    def to_schema_frame(self, raw: pl.DataFrame, params: dict[str, Any]) -> pl.DataFrame:
        if raw.is_empty():
            return raw

        df = raw.with_columns(
            [
                pl.col("ts_code").alias("instrument_id"),
                pl.col("ts_code").alias("source_code"),
                pl.lit("tushare").alias("source_id"),
                pl.col("vol").cast(pl.Float64, strict=False).alias("volume"),
                (pl.col("amount").cast(pl.Float64, strict=False) * 1000).alias("amount"),
                pl.lit(datetime.now()).alias("updated_at"),
            ]
        )
        return self.ensure_schema_columns(df)


class InstrumentETFDataset(BaseTushareDataset):
    name = "etf_list"
    table = REFERENCE_INSTRUMENT
    raw_unique_subset = ["ts_code"]
    raw_sort_by = ["ts_code"]

    def fetch_raw(self, params: dict[str, Any]) -> pl.DataFrame:
        return pandas_to_polars(self.client.get_etf_basic(**params))
    
    def to_schema_frame(self, raw: pl.DataFrame, params: dict[str, Any]) -> pl.DataFrame:
        if raw.is_empty():
            return raw
        
        df = raw.with_columns(
            [
                pl.col("ts_code").alias("instrument_id"),
                pl.lit("fund").alias("asset_class"),
                pl.lit("etf").alias("instrument_type"),
                pl.col("ts_code").alias("symbol"),
                pl.col("cname").alias("name"),
                pl.col("cname").alias("full_name"),
                pl.col("index_code").alias("index_code"),
                pl.col("exchange").alias("exchange"),
                pl.lit("CNY").alias("currency"),
                pl.col("ts_code").alias("source_code"),
                pl.lit("tushare").alias("source_id"),
                pl.lit(False).alias("is_hs"),
            ]
        )
        return self.ensure_schema_columns(df)


class MarketBarsETFDailyDataset(BaseTushareDataset):
    name = "market_bars_etf_daily"
    table = MARKET_BARS_DAILY
    raw_name = "fund_daily"
    raw_unique_subset = ["ts_code", "trade_date"]
    raw_sort_by = ["ts_code", "trade_date"]
    raw_date_col = "trade_date"
    db_date_col = "trade_date"
    incremental = True

    def fetch_incremental(self, params: dict[str, Any]) -> pl.DataFrame:
        start = DateParser.to_date(params["start"])
        end = DateParser.to_date(params["end"])
        instrument_ids = self.get_instrument_ids(params)
        ranges_by_instrument = self.get_update_ranges_by_instrument(instrument_ids, start, end)

        frames: list[pl.DataFrame] = []
        total = len(instrument_ids)
        fetch_ranges = 0
        fetched_rows = 0
        for idx, instrument_id in enumerate(instrument_ids, start=1):
            ranges = ranges_by_instrument.get(instrument_id, [])
            fetch_ranges += len(ranges)
            for fetch_start, fetch_end in ranges:
                raw = self.client.get_fund_daily(
                    ts_code=instrument_id,
                    start_date=fetch_start,
                    end_date=fetch_end,
                    fields=params.get("fields"),
                )
                if not raw.empty:
                    frame = pandas_to_polars(raw)
                    fetched_rows += frame.height
                    frames.append(frame)
            if idx % 200 == 0 or idx == total:
                logger.info(
                    f"market_bars_etf_daily checked {idx}/{total}, "
                    f"fetch_ranges={fetch_ranges}, fetched_rows={fetched_rows}. "
                )

        if not frames:
            logger.info(
                f"market_bars_etf_daily summary checked={total}, "
                f"fetch_ranges={fetch_ranges}, fetched_rows={fetched_rows}. "
            )
            return pl.DataFrame()
        logger.info(
            f"market_bars_etf_daily summary checked={total}, "
            f"fetch_ranges={fetch_ranges}, fetched_rows={fetched_rows}. "
        )
        return pl.concat(frames, how="diagonal_relaxed")

    def fetch_raw(self, params: dict[str, Any]) -> pl.DataFrame:
        return self.fetch_incremental(params)

    def get_instrument_ids(self, params: dict[str, Any]) -> list[str]:
        if params.get("ts_codes"):
            return sorted(params["ts_codes"])
        if params.get("ts_code"):
            return [params["ts_code"]]

        end = DateParser.to_date(params["end"])
        with connect(self.db_path) as con:
            ensure_all_tables(con)
            rows = con.execute(
                """
                SELECT instrument_id
                FROM reference_instrument
                WHERE asset_class = 'fund'
                  AND instrument_type = 'etf'
                  AND source_id = 'tushare'
                  AND list_status = 'L'
                  AND (list_date IS NULL OR list_date <= ?)
                  AND (
                    instrument_id LIKE '%.SZ'
                    OR instrument_id LIKE '%.SH'
                  )
                ORDER BY instrument_id
                """,
                [end],
            ).fetchall()

        instrument_ids = [row[0] for row in rows]
        if not instrument_ids:
            raise ValueError("No Tushare ETF instruments found. Run dataset `etf_list` first.")
        return instrument_ids

    def to_schema_frame(self, raw: pl.DataFrame, params: dict[str, Any]) -> pl.DataFrame:
        if raw.is_empty():
            return raw

        df = raw.with_columns(
            [
                pl.col("ts_code").alias("instrument_id"),
                pl.col("ts_code").alias("source_code"),
                pl.lit("tushare").alias("source_id"),
                pl.col("vol").cast(pl.Float64, strict=False).alias("volume"),
                (pl.col("amount").cast(pl.Float64, strict=False) * 1000).alias("amount"),
                pl.lit(datetime.now()).alias("updated_at"),
            ]
        )
        return self.ensure_schema_columns(df)


class InstrumentIndexDataset(BaseTushareDataset):
    name = "index_list"
    table = REFERENCE_INSTRUMENT
    raw_unique_subset = ["ts_code"]
    raw_sort_by = ["ts_code"]

    def fetch_raw(self, params: dict[str, Any]) -> pl.DataFrame:
        markets = params.get("markets") or params.get("market") or ["SSE", "SZSE", "CSI"]
        if isinstance(markets, str):
            markets = [markets]

        frames: list[pl.DataFrame] = []
        for market in markets:
            market_params = dict(params)
            market_params.pop("markets", None)
            market_params["market"] = market
            raw = self.client.get_index_basic(**market_params)
            if not raw.empty:
                frames.append(pandas_to_polars(raw))

        if not frames:
            return pl.DataFrame()
        return pl.concat(frames, how="diagonal_relaxed")
    
    def to_schema_frame(self, raw: pl.DataFrame, params: dict[str, Any]) -> pl.DataFrame:
        if raw.is_empty():
            return raw

        df = raw.with_columns(
            [
                pl.col("ts_code").alias("instrument_id"),
                pl.lit("index").alias("asset_class"),
                pl.lit("index").alias("instrument_type"),
                pl.col("ts_code").alias("symbol"),
                pl.col("fullname").alias("full_name"),
                pl.col("market").alias("exchange"),
                pl.lit("CNY").alias("currency"),
                pl.lit("L").alias("list_status"),
                pl.col("exp_date").alias("exp_date"),
                pl.col("ts_code").alias("source_code"),
                pl.lit("tushare").alias("source_id"),
                pl.lit(False).alias("is_hs"),
            ]
        )
        return self.ensure_schema_columns(df)


class MarketBarsIndexDaily(BaseTushareDataset):
    name = "market_bars_index_daily"
    table = MARKET_BARS_DAILY
    raw_name = "index_daily"
    raw_unique_subset = ["ts_code", "trade_date"]
    raw_sort_by = ["ts_code", "trade_date"]
    raw_date_col = "trade_date"
    db_date_col = "trade_date"
    incremental = True

    def fetch_incremental(self, params: dict[str, Any]) -> pl.DataFrame:
        start = DateParser.to_date(params["start"])
        end = DateParser.to_date(params["end"])
        api = self.get_api(params)
        instrument_ids = self.get_instrument_ids(params)

        frames: list[pl.DataFrame] = []
        total = len(instrument_ids)
        logger.info(f"market_bars_index_daily api={api} start={start} end={end} instruments={total}. ")
        for idx, instrument_id in enumerate(instrument_ids, start=1):
            ranges_by_instrument = self.get_update_ranges_by_instrument([instrument_id], start, end)
            ranges = ranges_by_instrument.get(instrument_id, [])
            for fetch_start, fetch_end in ranges:
                raw = self.fetch_index_raw(
                    ts_code=instrument_id,
                    start_date=fetch_start,
                    end_date=fetch_end,
                    fields=params.get("fields"),
                    params=params,
                )
                if not raw.empty:
                    frame = pandas_to_polars(raw)
                    # # 兼容没有amount列的数据
                    # if "amount" not in frame.columns:
                    #     frame = frame.with_columns(pl.lit(None, dtype=pl.Float64).alias("amount"))
                    frames.append(frame)
            if idx % 200 == 0 or idx == total:
                logger.info(
                    f"market_bars_index_daily api={api} progress {idx}/{total}, "
                    f"current={instrument_id}. "
                )

        if not frames:
            return pl.DataFrame()
        return pl.concat(frames, how="diagonal_relaxed")

    def fetch_raw(self, params: dict[str, Any]) -> pl.DataFrame:
        return self.fetch_incremental(params)

    def get_api(self, params: dict[str, Any]) -> str:
        return params.get("api") or params.get("index_api") or "index_daily"

    def fetch_index_raw(
        self,
        ts_code: str,
        start_date: date,
        end_date: date,
        fields: list[str] | None,
        params: dict[str, Any],
    ) -> pd.DataFrame:
        api = self.get_api(params)
        if api == "index_daily":
            return self.client.get_index_daily(
                ts_code=ts_code,
                start_date=start_date,
                end_date=end_date,
                fields=fields,
            )
        if api == "index_global":
            return self.client.get_index_global(
                ts_code=ts_code,
                start_date=start_date,
                end_date=end_date,
                fields=fields,
            )
        if api == "fut_index_daily":
            return self.client.get_future_index_daily(
                ts_code=ts_code,
                start_date=start_date,
                end_date=end_date,
                fields=fields,
            )
        raise ValueError(f"Unsupported Tushare index api: {api}")

    def get_instrument_ids(self, params: dict[str, Any]) -> list[str]:
        if params.get("index_codes"):
            index_codes = params["index_codes"]
            if isinstance(index_codes, str):
                return [index_codes]
            return sorted(index_codes)
        if params.get("ts_codes"):
            return sorted(params["ts_codes"])
        if params.get("ts_code"):
            return [params["ts_code"]]

        end = DateParser.to_date(params["end"])
        with connect(self.db_path) as con:
            ensure_all_tables(con)
            rows = con.execute(
                """
                SELECT instrument_id
                FROM reference_instrument
                WHERE asset_class = 'index'
                  AND instrument_type = 'index'
                  AND source_id = 'tushare'
                  AND list_status = 'L'
                  AND list_date IS NOT NULL
                  AND list_date <= ?
                  AND (exp_date IS NULL OR exp_date > ?)
                  AND (
                    instrument_id LIKE '%.SH'
                    OR instrument_id LIKE '%.SZ'
                    OR regexp_matches(instrument_id, '^[0-9]{6}\\.CSI$')
                  )
                  AND NOT regexp_matches(instrument_id, '(USD|HKD|GBP|EUR|CAD|CNH)')
                  AND NOT regexp_matches(coalesce(name, ''), '(全收益|净收益|退市|USD|HKD|GBP|EUR|CAD|CNH)')
                  AND NOT regexp_matches(coalesce(full_name, ''), '(全收益|净收益|退市|USD|HKD|GBP|EUR|CAD|CNH)')
                ORDER BY instrument_id
                """,
                [end, end],
            ).fetchall()

        instrument_ids = [row[0] for row in rows]
        if not instrument_ids:
            raise ValueError("No Tushare index instruments found. Run dataset `index_list` first.")
        return instrument_ids

    def to_schema_frame(self, raw: pl.DataFrame, params: dict[str, Any]) -> pl.DataFrame:
        if raw.is_empty():
            return raw

        df = raw.with_columns(
            [
                pl.col("ts_code").alias("instrument_id"),
                pl.col("ts_code").alias("source_code"),
                pl.lit("tushare").alias("source_id"),
                pl.col("vol").cast(pl.Float64, strict=False).alias("volume"),
                (pl.col("amount").cast(pl.Float64, strict=False) * 1000).alias("amount"),
                pl.lit(datetime.now()).alias("updated_at"),
            ]
        )
        return self.ensure_schema_columns(df)



class InstrumentFutureDataset(BaseTushareDataset):
    name = "future_list"
    table = [REFERENCE_INSTRUMENT, REFERENCE_FUTURE]
    raw_name = "future_basic"
    raw_unique_subset = ["ts_code"]
    raw_sort_by = ["ts_code"]
    default_exchanges = ["CFFEX", "DCE", "CZCE", "SHFE", "INE", "GFEX"]

    def update(self, **params: Any) -> LoadResult:
        raw_update = self.fetch_incremental(params)
        if raw_update.is_empty():
            return LoadResult(
                dataset=self.name,
                raw_path=self.raw_path,
                db_path=self.db_path,
                raw_rows=0,
                rows_written=0,
            )

        UpdateLocalParquet.update(
            self.raw_path,
            raw_update,
            unique_subset=self.raw_unique_subset,
            sort_by=self.raw_sort_by,
        )

        instrument_frame = REFERENCE_INSTRUMENT.model.format_polars(
            self.to_reference_instrument_frame(raw_update, params)
        )
        future_frame = REFERENCE_FUTURE.model.format_polars(
            self.to_reference_future_frame(raw_update, params)
        )

        with connect(self.db_path) as con:
            ensure_all_tables(con)
            rows_written = upsert_frame(con, REFERENCE_INSTRUMENT, instrument_frame)
            rows_written += upsert_frame(con, REFERENCE_FUTURE, future_frame)

        return LoadResult(
            dataset=self.name,
            raw_path=self.raw_path,
            db_path=self.db_path,
            raw_rows=raw_update.height,
            rows_written=rows_written,
        )

    def fetch_raw(self, params: dict[str, Any]) -> pl.DataFrame:
        frames: list[pl.DataFrame] = []
        for exchange in self.get_exchanges(params):
            exchange_params = dict(params)
            exchange_params.pop("exchanges", None)
            exchange_params["exchange"] = exchange
            raw = self.client.get_future_basic(**exchange_params)
            if not raw.empty:
                frames.append(pandas_to_polars(raw))

        if not frames:
            return pl.DataFrame()
        return pl.concat(frames, how="diagonal_relaxed")

    def get_exchanges(self, params: dict[str, Any]) -> list[str]:
        exchanges = params.get("exchanges") or params.get("exchange") or self.default_exchanges
        if isinstance(exchanges, str):
            return [exchanges]
        return list(exchanges)

    def to_schema_frame(self, raw: pl.DataFrame, params: dict[str, Any]) -> pl.DataFrame:
        return self.to_reference_instrument_frame(raw, params)

    def to_reference_instrument_frame(
        self,
        raw: pl.DataFrame,
        params: dict[str, Any],
    ) -> pl.DataFrame:
        if raw.is_empty():
            return raw

        df = raw.with_columns(
            [
                pl.col("ts_code").alias("instrument_id"),
                pl.lit("future").alias("asset_class"),
                pl.lit("future").alias("instrument_type"),
                pl.col("symbol").alias("symbol"),
                pl.col("name").alias("name"),
                pl.col("name").alias("full_name"),
                pl.col("exchange").alias("exchange"),
                pl.lit("CNY").alias("currency"),
                pl.col("ts_code").alias("source_code"),
                pl.lit("tushare").alias("source_id"),
                pl.lit(False).alias("is_hs"),
            ]
        )
        return self.ensure_schema_columns(df, REFERENCE_INSTRUMENT)

    def to_reference_future_frame(
        self,
        raw: pl.DataFrame,
        params: dict[str, Any],
    ) -> pl.DataFrame:
        if raw.is_empty():
            return raw

        df = raw.with_columns(
            [
                pl.col("ts_code").alias("instrument_id"),
                pl.col("ts_code").alias("source_code"),
                pl.lit("tushare").alias("source_id"),
                pl.lit(datetime.now()).alias("updated_at"),
            ]
        )
        return self.ensure_schema_columns(df, REFERENCE_FUTURE)


class MarketBarsFutureDailyDataset(BaseTushareDataset):
    name = "market_bars_future_daily"
    table = MARKET_BARS_DERIVATIVE_DAILY
    raw_name = "future_daily"
    raw_unique_subset = ["ts_code", "trade_date"]
    raw_sort_by = ["ts_code", "trade_date"]
    raw_date_col = "trade_date"
    db_date_col = "trade_date"
    incremental = True

    def fetch_incremental(self, params: dict[str, Any]) -> pl.DataFrame:
        start = DateParser.to_date(params["start"])
        end = DateParser.to_date(params["end"])
        instrument_ids = self.get_instrument_ids(params)

        frames: list[pl.DataFrame] = []
        total = len(instrument_ids)
        for idx, instrument_id in enumerate(instrument_ids, start=1):
            ranges_by_instrument = self.get_update_ranges_by_instrument([instrument_id], start, end)
            ranges = ranges_by_instrument.get(instrument_id, [])
            for fetch_start, fetch_end in ranges:
                raw = self.client.get_future_daily(
                    ts_code=instrument_id,
                    start_date=fetch_start,
                    end_date=fetch_end,
                    fields=params.get("fields"),
                )
                if not raw.empty:
                    frames.append(pandas_to_polars(raw))
            if idx == 1 or idx % 200 == 0 or idx == total:
                logger.info(f"market_bars_future_daily progress {idx}/{total}. ")

        if not frames:
            return pl.DataFrame()
        return pl.concat(frames, how="diagonal_relaxed")

    def fetch_raw(self, params: dict[str, Any]) -> pl.DataFrame:
        return self.fetch_incremental(params)

    def get_instrument_ids(self, params: dict[str, Any]) -> list[str]:
        if params.get("ts_codes"):
            return sorted(params["ts_codes"])
        if params.get("ts_code"):
            return [params["ts_code"]]

        start = DateParser.to_date(params["start"])
        end = DateParser.to_date(params["end"])

        with connect(self.db_path) as con:
            ensure_all_tables(con)
            rows = con.execute(
                """
                SELECT instrument_id
                FROM reference_future
                WHERE source_id = 'tushare'
                  AND list_date IS NOT NULL
                  AND list_date <= ?
                  AND (
                    coalesce(last_ddate, delist_date) IS NULL
                    OR coalesce(last_ddate, delist_date) >= ?
                  )
                ORDER BY instrument_id
                """,
                [end, start],
            ).fetchall()

        instrument_ids = [row[0] for row in rows]
        if not instrument_ids:
            raise ValueError("No Tushare future instruments found. Run dataset `future_list` first.")
        return instrument_ids

    def db_update_filters(self, params: dict[str, Any]) -> dict[str, Any] | None:
        return {"source_id": "tushare"}

    def to_schema_frame(self, raw: pl.DataFrame, params: dict[str, Any]) -> pl.DataFrame:
        if raw.is_empty():
            return raw

        df = raw.with_columns(
            [
                pl.col("ts_code").alias("instrument_id"),
                pl.col("ts_code").alias("source_code"),
                pl.lit("tushare").alias("source_id"),
                pl.lit(datetime.now()).alias("updated_at"),
            ]
        )
        return self.ensure_schema_columns(df)


class MarketFXDailyDataset(BaseTushareDataset):
    name = "market_fx_daily"
    table = MARKET_FX_DAILY
    raw_name = "fx_daily"
    raw_unique_subset = ["ts_code", "trade_date"]
    raw_sort_by = ["ts_code", "trade_date"]
    raw_date_col = "trade_date"
    db_date_col = "trade_date"
    incremental = True

    def update(self, **params: Any) -> LoadResult:
        raw_update = self.fetch_incremental(params)
        if raw_update.is_empty():
            return LoadResult(
                dataset=self.name,
                raw_path=self.raw_path,
                db_path=self.db_path,
                raw_rows=0,
                rows_written=0,
            )

        UpdateLocalParquet.update(
            self.raw_path,
            raw_update,
            unique_subset=self.raw_unique_subset,
            sort_by=self.raw_sort_by,
        )

        reference_frame = REFERENCE_INSTRUMENT.model.format_polars(
            self.to_reference_instrument_frame(raw_update, params)
        )
        fx_frame = MARKET_FX_DAILY.model.format_polars(
            self.to_schema_frame(raw_update, params)
        )

        with connect(self.db_path) as con:
            ensure_all_tables(con)
            rows_written = upsert_frame(con, REFERENCE_INSTRUMENT, reference_frame)
            rows_written += upsert_frame(con, MARKET_FX_DAILY, fx_frame)

        return LoadResult(
            dataset=self.name,
            raw_path=self.raw_path,
            db_path=self.db_path,
            raw_rows=raw_update.height,
            rows_written=rows_written,
        )

    def fetch_incremental(self, params: dict[str, Any]) -> pl.DataFrame:
        start = DateParser.to_date(params["start"])
        end = DateParser.to_date(params["end"])
        instrument_ids = self.get_instrument_ids(params)

        frames: list[pl.DataFrame] = []
        total = len(instrument_ids)
        for idx, instrument_id in enumerate(instrument_ids, start=1):
            ranges_by_instrument = self.get_update_ranges_by_instrument([instrument_id], start, end)
            ranges = ranges_by_instrument.get(instrument_id, [])
            for fetch_start, fetch_end in ranges:
                raw = self.client.get_fx_daily(
                    ts_code=instrument_id,
                    start_date=fetch_start,
                    end_date=fetch_end,
                    exchange=params.get("exchange"),
                    fields=params.get("fields"),
                )
                if not raw.empty:
                    frames.append(pandas_to_polars(raw))
            if idx == 1 or idx % 200 == 0 or idx == total:
                logger.info(f"market_fx_daily progress {idx}/{total}. ")

        if not frames:
            return pl.DataFrame()
        return pl.concat(frames, how="diagonal_relaxed")

    def fetch_raw(self, params: dict[str, Any]) -> pl.DataFrame:
        return self.fetch_incremental(params)

    def get_instrument_ids(self, params: dict[str, Any]) -> list[str]:
        codes = params.get("ts_codes") or params.get("ts_code")
        if codes is None:
            raise ValueError("market_fx_daily requires `ts_codes` or `ts_code`.")
        if isinstance(codes, str):
            return [codes]
        return sorted(codes)

    def to_reference_instrument_frame(
        self,
        raw: pl.DataFrame,
        params: dict[str, Any],
    ) -> pl.DataFrame:
        if raw.is_empty():
            return raw

        df = raw.select("ts_code").unique().with_columns(
            [
                pl.col("ts_code").alias("instrument_id"),
                pl.col("ts_code").alias("symbol"),
                pl.col("ts_code").alias("name"),
                pl.col("ts_code").alias("full_name"),
                pl.lit("FX").alias("market"),
                pl.col("ts_code")
                .str.extract(r"\.([^.]+)$", 1)
                .fill_null(params.get("exchange") or "FX")
                .alias("exchange"),
                pl.col("ts_code").str.extract(r"^[A-Z]{3}([A-Z]{3})", 1).alias("currency"),
                pl.lit("L").alias("list_status"),
                pl.lit(False).alias("is_hs"),
                pl.col("ts_code").alias("source_code"),
                pl.lit("tushare").alias("source_id"),
                pl.lit("fx").alias("asset_class"),
                pl.lit("currency_pair").alias("instrument_type"),
            ]
        )
        return self.ensure_schema_columns(df, REFERENCE_INSTRUMENT)

    def to_schema_frame(self, raw: pl.DataFrame, params: dict[str, Any]) -> pl.DataFrame:
        if raw.is_empty():
            return raw

        df = self.ensure_fx_quote_columns(raw).with_columns(
            [
                pl.col("ts_code").alias("instrument_id"),
                pl.col("ts_code").alias("source_code"),
                pl.lit("tushare").alias("source_id"),
                pl.col("bid_open").cast(pl.Float64, strict=False).alias("bid_open"),
                pl.col("bid_high").cast(pl.Float64, strict=False).alias("bid_high"),
                pl.col("bid_low").cast(pl.Float64, strict=False).alias("bid_low"),
                pl.col("bid_close").cast(pl.Float64, strict=False).alias("bid_close"),
                pl.col("ask_open").cast(pl.Float64, strict=False).alias("ask_open"),
                pl.col("ask_high").cast(pl.Float64, strict=False).alias("ask_high"),
                pl.col("ask_low").cast(pl.Float64, strict=False).alias("ask_low"),
                pl.col("ask_close").cast(pl.Float64, strict=False).alias("ask_close"),
                (
                    (
                        pl.col("bid_open").cast(pl.Float64, strict=False)
                        + pl.col("ask_open").cast(pl.Float64, strict=False)
                    )
                    / 2
                ).alias("mid_open"),
                (
                    (
                        pl.col("bid_high").cast(pl.Float64, strict=False)
                        + pl.col("ask_high").cast(pl.Float64, strict=False)
                    )
                    / 2
                ).alias("mid_high"),
                (
                    (
                        pl.col("bid_low").cast(pl.Float64, strict=False)
                        + pl.col("ask_low").cast(pl.Float64, strict=False)
                    )
                    / 2
                ).alias("mid_low"),
                (
                    (
                        pl.col("bid_close").cast(pl.Float64, strict=False)
                        + pl.col("ask_close").cast(pl.Float64, strict=False)
                    )
                    / 2
                ).alias("mid_close"),
                (
                    pl.col("ask_close").cast(pl.Float64, strict=False)
                    - pl.col("bid_close").cast(pl.Float64, strict=False)
                ).alias("spread_close"),
                pl.lit(datetime.now()).alias("updated_at"),
            ]
        )
        return self.ensure_schema_columns(df)

    def ensure_fx_quote_columns(self, df: pl.DataFrame) -> pl.DataFrame:
        for column in [
            "bid_open",
            "bid_high",
            "bid_low",
            "bid_close",
            "ask_open",
            "ask_high",
            "ask_low",
            "ask_close",
        ]:
            if column not in df.columns:
                df = df.with_columns(pl.lit(None, dtype=pl.Float64).alias(column))
        return df



DATASET_CLASSES: dict[str, type[BaseTushareDataset]] = {
    InstrumentStockDataset.name: InstrumentStockDataset,
    InstrumentStockStDataset.name: InstrumentStockStDataset,
    MarketBarsStockDailyDataset.name: MarketBarsStockDailyDataset,
    InstrumentETFDataset.name: InstrumentETFDataset,
    MarketBarsETFDailyDataset.name: MarketBarsETFDailyDataset,
    InstrumentIndexDataset.name: InstrumentIndexDataset,
    MarketBarsIndexDaily.name: MarketBarsIndexDaily,
    InstrumentFutureDataset.name: InstrumentFutureDataset,
    MarketBarsFutureDailyDataset.name: MarketBarsFutureDailyDataset,
    MarketFXDailyDataset.name: MarketFXDailyDataset,
}


class TushareLoader:
    def __init__(
        self,
        client: TushareClient | None = None,
        root_dir: str | Path | None = None,
        db_path: str | Path | None = None,
    ) -> None:
        self.client = client or TushareClient()
        self.root_dir = data_dir(root_dir)
        self.db_path = Path(db_path).expanduser().resolve() if db_path else default_db_path(self.root_dir)
        self.datasets = {
            name: dataset_cls(self.client, self.root_dir, self.db_path)
            for name, dataset_cls in DATASET_CLASSES.items()
        }

    def update(self, dataset: str, **params: Any) -> LoadResult:
        return self.datasets[dataset].update(**params)


TUSHARE_DATASETS = DATASET_CLASSES
