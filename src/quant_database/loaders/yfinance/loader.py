from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import polars as pl

from quant_database.core.bootstrap import ensure_all_tables
from quant_database.core.connection import connect
from quant_database.core.upsert import upsert_frame
from quant_database.providers.tushare_client import find_project_root
from quant_database.providers.yfinance_client import YFinanceClient
from quant_database.schema.base import TableSchema
from quant_database.schema.market import MARKET_BARS_DAILY
from quant_database.schema.reference import REFERENCE_INSTRUMENT
from utils.logger import logger
from utils.tools import UpdateLocalParquet


@dataclass(frozen=True)
class LoadResult:
    dataset: str
    raw_path: Path
    db_path: Path
    raw_rows: int
    rows_written: int


@dataclass(frozen=True)
class YFinanceTickerInfo:
    ticker: str
    valid: bool
    instrument_id: str | None
    symbol: str | None
    name: str | None
    full_name: str | None
    exchange: str | None
    market: str | None
    quote_type: str | None
    asset_class: str | None
    instrument_type: str | None
    source_code: str
    source_id: str
    reason: str | None


def data_dir(root_dir: str | Path | None = None) -> Path:
    if root_dir is None:
        return find_project_root() / "data"
    return Path(root_dir).expanduser().resolve()


def default_db_path(root_dir: str | Path | None = None) -> Path:
    return data_dir(root_dir) / "quant.duckdb"


def raw_parquet_path(
    table_name: str,
    source_id: str = "yfinance",
    root_dir: str | Path | None = None,
) -> Path:
    return data_dir(root_dir) / "raw" / source_id / f"{table_name}.parquet"


def pandas_to_polars(df: pd.DataFrame) -> pl.DataFrame:
    return pl.from_pandas(df)


def _as_tickers(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value]


def _quote_type_to_asset_class(quote_type: Any) -> str:
    value = str(quote_type or "").upper()
    mapping = {
        "EQUITY": "equity",
        "ETF": "fund",
        "MUTUALFUND": "fund",
        "INDEX": "index",
        "FUTURE": "future",
        "CURRENCY": "currency",
        "CRYPTOCURRENCY": "crypto",
    }
    return mapping.get(value, "unknown")


def _quote_type_to_instrument_type(quote_type: Any) -> str:
    value = str(quote_type or "").upper()
    mapping = {
        "EQUITY": "stock",
        "ETF": "etf",
        "MUTUALFUND": "mutual_fund",
        "INDEX": "index",
        "FUTURE": "future",
        "CURRENCY": "currency",
        "CRYPTOCURRENCY": "crypto",
    }
    return mapping.get(value, "unknown")


def _ticker_infos_to_polars(infos: list[YFinanceTickerInfo]) -> pl.DataFrame:
    return pl.DataFrame([asdict(info) for info in infos])


def _valid_tickers(infos: list[YFinanceTickerInfo]) -> list[str]:
    valid: list[str] = []
    for info in infos:
        if info.valid and info.instrument_id:
            valid.append(info.instrument_id)
            continue
        logger.warning(
            "Skip invalid yfinance ticker "
            f"{info.ticker}: {info.reason or 'no exact symbol match'}"
        )
    return valid


class YFinanceTickerValidator:
    source_id = "yfinance"

    def __init__(self, client: YFinanceClient) -> None:
        self.client = client

    def validate(self, tickers: list[str] | tuple[str, ...]) -> list[YFinanceTickerInfo]:
        validation = self.client.validate_tickers(list(tickers))
        if validation.empty:
            return []

        infos: list[YFinanceTickerInfo] = []
        for _, row in validation.iterrows():
            ticker = str(row.get("ticker"))
            found = bool(row.get("found"))
            matched_symbol = row.get("matched_symbol")
            instrument_id = str(matched_symbol) if found and pd.notna(matched_symbol) else None
            quote_type = row.get("quote_type")
            name = row.get("short_name")
            if pd.isna(name):
                name = instrument_id
            exchange = row.get("exchange")
            if pd.isna(exchange):
                exchange = None

            infos.append(
                YFinanceTickerInfo(
                    ticker=ticker,
                    valid=found and instrument_id is not None,
                    instrument_id=instrument_id,
                    symbol=instrument_id,
                    name=str(name) if name is not None else None,
                    full_name=str(name) if name is not None else None,
                    exchange=str(exchange) if exchange is not None else None,
                    market=str(exchange) if exchange is not None else None,
                    quote_type=str(quote_type) if pd.notna(quote_type) else None,
                    asset_class=_quote_type_to_asset_class(quote_type),
                    instrument_type=_quote_type_to_instrument_type(quote_type),
                    source_code=ticker,
                    source_id=self.source_id,
                    reason=row.get("reason") if pd.notna(row.get("reason")) else None,
                )
            )
        return infos


class BaseYFinanceDataset:
    name: str
    table: TableSchema
    raw_name: str
    raw_unique_subset: list[str]
    raw_sort_by: list[str]
    source_id = "yfinance"

    def __init__(
        self,
        client: YFinanceClient,
        root_dir: str | Path,
        db_path: str | Path,
    ) -> None:
        self.client = client
        self.validator = YFinanceTickerValidator(client)
        self.root_dir = Path(root_dir).expanduser().resolve()
        self.db_path = Path(db_path).expanduser().resolve()

    @property
    def raw_path(self) -> Path:
        return raw_parquet_path(self.raw_name, source_id=self.source_id, root_dir=self.root_dir)

    def update(self, **params: Any) -> LoadResult:
        raw_update = self.fetch_raw(params)
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
        raw_for_write = self.filter_raw_for_write(raw_update, params)
        if raw_for_write.is_empty():
            return LoadResult(
                dataset=self.name,
                raw_path=self.raw_path,
                db_path=self.db_path,
                raw_rows=raw_update.height,
                rows_written=0,
            )

        formatted = self.table.model.format_polars(self.to_schema_frame(raw_for_write, params))

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

    def fetch_raw(self, params: dict[str, Any]) -> pl.DataFrame:
        raise NotImplementedError

    def to_schema_frame(self, raw: pl.DataFrame, params: dict[str, Any]) -> pl.DataFrame:
        raise NotImplementedError

    def filter_raw_for_write(self, raw: pl.DataFrame, params: dict[str, Any]) -> pl.DataFrame:
        return raw

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


class InstrumentYFinanceDataset(BaseYFinanceDataset):
    name = "yf_instrument"
    table = REFERENCE_INSTRUMENT
    raw_name = "reference_instrument"
    raw_unique_subset = ["ticker"]
    raw_sort_by = ["ticker"]

    def fetch_raw(self, params: dict[str, Any]) -> pl.DataFrame:
        tickers = _as_tickers(params.get("tickers") or params.get("ticker"))
        if not tickers:
            raise ValueError("yfinance instrument dataset requires `tickers`.")
        return _ticker_infos_to_polars(self.validator.validate(tickers))

    def to_schema_frame(self, raw: pl.DataFrame, params: dict[str, Any]) -> pl.DataFrame:
        if raw.is_empty():
            return raw

        currency = params.get("currency", "USD")
        df = raw.filter(pl.col("valid") == True).with_columns(
            [
                pl.col("instrument_id"),
                pl.col("symbol"),
                pl.col("name"),
                pl.col("full_name"),
                pl.col("exchange"),
                pl.col("market"),
                pl.lit(currency).alias("currency"),
                pl.lit("L").alias("list_status"),
                pl.lit(False).alias("is_hs"),
                pl.col("source_code"),
                pl.col("source_id"),
                pl.col("asset_class"),
                pl.col("instrument_type"),
            ]
        )
        return self.ensure_schema_columns(df)


class MarketBarsYFinanceDailyDataset(BaseYFinanceDataset):
    name = "yf_market_bars_daily"
    table = MARKET_BARS_DAILY
    raw_name = "daily"
    raw_unique_subset = ["ticker", "date"]
    raw_sort_by = ["ticker", "date"]

    def fetch_raw(self, params: dict[str, Any]) -> pl.DataFrame:
        tickers = self.get_tickers(params)
        if not tickers:
            return pl.DataFrame()

        history_params = {
            "tickers": tickers,
            "start": params.get("start"),
            "end": params.get("end"),
            "period": params.get("period"),
            "interval": params.get("interval", "1d"),
            "auto_adjust": params.get("auto_adjust", False),
            "actions": params.get("actions", False),
            "threads": params.get("threads", True),
            "repair": params.get("repair", False),
            "keepna": params.get("keepna", False),
            "progress": params.get("progress", False),
        }
        return pandas_to_polars(self.client.download_history(**history_params))

    def get_tickers(self, params: dict[str, Any]) -> list[str]:
        tickers = _as_tickers(params.get("tickers") or params.get("ticker"))
        if tickers:
            return _valid_tickers(self.validator.validate(tickers))

        with connect(self.db_path) as con:
            ensure_all_tables(con)
            rows = con.execute(
                """
                SELECT instrument_id
                FROM reference_instrument
                WHERE source_id = 'yfinance'
                  AND list_status = 'L'
                ORDER BY instrument_id
                """
            ).fetchall()

        instrument_ids = [row[0] for row in rows]
        if not instrument_ids:
            raise ValueError("No yfinance instruments found. Run dataset `yf_instrument` first or pass `tickers`.")
        return instrument_ids

    def filter_raw_for_write(self, raw: pl.DataFrame, params: dict[str, Any]) -> pl.DataFrame:
        if raw.is_empty():
            return raw

        incoming_keys = (
            raw.select(
                [
                    pl.col("ticker").alias("instrument_id"),
                    pl.col("date").cast(pl.Date, strict=False).alias("trade_date"),
                ]
            )
            .drop_nulls(["instrument_id", "trade_date"])
            .unique()
        )
        if incoming_keys.is_empty():
            return raw

        instrument_ids = incoming_keys["instrument_id"].unique().sort().to_list()
        start = incoming_keys["trade_date"].min()
        end = incoming_keys["trade_date"].max()
        existing_keys = self.get_existing_market_keys(instrument_ids, start, end)
        if existing_keys.is_empty():
            return raw

        return (
            raw.with_columns(
                [
                    pl.col("ticker").alias("instrument_id"),
                    pl.col("date").cast(pl.Date, strict=False).alias("trade_date"),
                ]
            )
            .join(existing_keys, on=["instrument_id", "trade_date"], how="anti")
            .drop(["instrument_id", "trade_date"])
        )

    def get_existing_market_keys(
        self,
        instrument_ids: list[str],
        start: Any,
        end: Any,
    ) -> pl.DataFrame:
        if not instrument_ids:
            return pl.DataFrame({"instrument_id": [], "trade_date": []})

        placeholders = ", ".join(["?"] * len(instrument_ids))
        with connect(self.db_path) as con:
            ensure_all_tables(con)
            rows = con.execute(
                f"""
                SELECT instrument_id, trade_date
                FROM market_bars_daily
                WHERE source_id = 'yfinance'
                  AND instrument_id IN ({placeholders})
                  AND trade_date BETWEEN ? AND ?
                """,
                [*instrument_ids, start, end],
            ).fetchall()

        if not rows:
            return pl.DataFrame({"instrument_id": [], "trade_date": []})
        return pl.DataFrame(rows, schema=["instrument_id", "trade_date"], orient="row")

    def to_schema_frame(self, raw: pl.DataFrame, params: dict[str, Any]) -> pl.DataFrame:
        if raw.is_empty():
            return raw

        df = raw.sort(["ticker", "date"]).with_columns(
            [
                pl.col("ticker").alias("instrument_id"),
                pl.col("date").cast(pl.Date, strict=False).alias("trade_date"),
                pl.col("ticker").alias("source_code"),
                pl.lit(self.source_id).alias("source_id"),
                pl.col("volume").cast(pl.Float64, strict=False).alias("volume"),
                pl.lit(None, dtype=pl.Float64).alias("amount"),
                pl.col("close").shift(1).over("ticker").alias("pre_close"),
                pl.lit(datetime.now()).alias("updated_at"),
            ]
        )
        df = df.with_columns(
            [
                (pl.col("close") - pl.col("pre_close")).alias("change"),
                pl.when(pl.col("pre_close").is_not_null() & (pl.col("pre_close") != 0))
                .then((pl.col("close") - pl.col("pre_close")) / pl.col("pre_close"))
                .otherwise(None)
                .alias("pct_chg"),
            ]
        )
        return self.ensure_schema_columns(df)


DATASET_CLASSES = {
    InstrumentYFinanceDataset.name: InstrumentYFinanceDataset,
    MarketBarsYFinanceDailyDataset.name: MarketBarsYFinanceDailyDataset,
}


class YFinanceLoader:
    def __init__(
        self,
        client: YFinanceClient | None = None,
        root_dir: str | Path | None = None,
        db_path: str | Path | None = None,
    ) -> None:
        self.client = client or YFinanceClient()
        self.root_dir = data_dir(root_dir)
        self.db_path = Path(db_path).expanduser().resolve() if db_path else default_db_path(self.root_dir)
        self.datasets = {
            name: dataset_cls(self.client, self.root_dir, self.db_path)
            for name, dataset_cls in DATASET_CLASSES.items()
        }

    def update(self, dataset: str, **params: Any) -> LoadResult:
        return self.datasets[dataset].update(**params)
