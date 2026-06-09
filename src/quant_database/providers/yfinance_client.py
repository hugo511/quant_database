from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd
import yfinance as yf


def _date_to_str(value: date | datetime | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return value.strftime("%Y-%m-%d")


def _exclusive_end(value: date | datetime | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return (value + timedelta(days=1)).strftime("%Y-%m-%d")


def _as_list(value: str | list[str] | tuple[str, ...]) -> list[str]:
    if isinstance(value, str):
        return [value]
    return list(value)


def _records_to_frame(records: Any) -> pd.DataFrame:
    if records is None:
        return pd.DataFrame()
    if isinstance(records, pd.DataFrame):
        return records.reset_index(drop=True)
    return pd.DataFrame(list(records))


def _normalise_column_name(column: Any) -> str:
    text = str(column).strip().lower()
    return (
        text.replace(" ", "_")
        .replace("-", "_")
        .replace("/", "_")
        .replace(".", "_")
    )


def _first_present(*values: Any) -> Any:
    for value in values:
        if pd.notna(value):
            return value
    return None


@dataclass(frozen=True)
class TickerValidationResult:
    ticker: str
    found: bool
    matched_symbol: str | None
    quote_type: str | None
    exchange: str | None
    short_name: str | None
    reason: str | None


class YFinanceSearchClient:
    """Small wrapper around yfinance Search and Lookup APIs."""

    def __init__(self, session: Any = None, timeout: int = 30) -> None:
        self.session = session
        self.timeout = timeout

    def search(
        self,
        query: str,
        max_results: int = 8,
        news_count: int = 0,
        lists_count: int = 0,
        raise_errors: bool = False,
    ) -> pd.DataFrame:
        search = yf.Search(
            query,
            max_results=max_results,
            news_count=news_count,
            lists_count=lists_count,
            session=self.session,
            timeout=self.timeout,
            raise_errors=raise_errors,
        )
        return _records_to_frame(search.quotes)

    def lookup(
        self,
        query: str,
        count: int = 25,
        asset_type: str = "all",
        raise_errors: bool = False,
    ) -> pd.DataFrame:
        lookup = yf.Lookup(
            query,
            session=self.session,
            timeout=self.timeout,
            raise_errors=raise_errors,
        )
        method_name = f"get_{asset_type}"
        if asset_type == "all":
            method_name = "get_all"
        if not hasattr(lookup, method_name):
            raise ValueError(f"Unsupported yfinance lookup asset_type: {asset_type}")
        return _records_to_frame(getattr(lookup, method_name)(count=count))

    def validate_tickers(self, tickers: list[str] | tuple[str, ...]) -> pd.DataFrame:
        rows: list[TickerValidationResult] = []
        for ticker in tickers:
            quotes = self.search(ticker, max_results=5)
            if quotes.empty or "symbol" not in quotes.columns:
                rows.append(
                    TickerValidationResult(
                        ticker=ticker,
                        found=False,
                        matched_symbol=None,
                        quote_type=None,
                        exchange=None,
                        short_name=None,
                        reason="No quote found",
                    )
                )
                continue

            exact = quotes[quotes["symbol"].astype(str).str.upper() == ticker.upper()]
            match = exact.iloc[0] if not exact.empty else quotes.iloc[0]
            matched_symbol = str(match.get("symbol")) if pd.notna(match.get("symbol")) else None
            rows.append(
                TickerValidationResult(
                    ticker=ticker,
                    found=matched_symbol is not None and matched_symbol.upper() == ticker.upper(),
                    matched_symbol=matched_symbol,
                    quote_type=match.get("quoteType"),
                    exchange=match.get("exchange"),
                    short_name=_first_present(match.get("shortname"), match.get("shortName")),
                    reason=None,
                )
            )
        return pd.DataFrame([row.__dict__ for row in rows])


class YFinanceMarketDataClient:
    """Download and flatten Yahoo Finance historical market data."""

    def __init__(self, session: Any = None, timeout: int | float = 10) -> None:
        self.session = session
        self.timeout = timeout

    def download_history(
        self,
        tickers: str | list[str] | tuple[str, ...],
        start: date | datetime | str | None = None,
        end: date | datetime | str | None = None,
        period: str | None = None,
        interval: str = "1d",
        auto_adjust: bool = False,
        actions: bool = False,
        threads: bool | int = True,
        repair: bool = False,
        keepna: bool = False,
        progress: bool = False,
    ) -> pd.DataFrame:
        """
        yfinance.download https://ranaroussi.github.io/yfinance/reference/api/yfinance.download.html#yfinance.download
        """
        ticker_list = _as_list(tickers)
        raw = yf.download(
            tickers=ticker_list if len(ticker_list) > 1 else ticker_list[0],
            start=_date_to_str(start),
            end=_exclusive_end(end),
            period=period,
            interval=interval,
            group_by="ticker",
            auto_adjust=auto_adjust,
            actions=actions,
            threads=threads,
            repair=repair,
            keepna=keepna,
            progress=progress,
            timeout=self.timeout,
            session=self.session,
            multi_level_index=True,
        )
        return self.flatten_history(raw, ticker_list)

    def flatten_history(self, data: pd.DataFrame | None, tickers: list[str]) -> pd.DataFrame:
        if data is None or data.empty:
            return self.empty_history_frame()

        frames: list[pd.DataFrame] = []
        if isinstance(data.columns, pd.MultiIndex):
            level0 = set(map(str, data.columns.get_level_values(0)))
            ticker_first = any(ticker in level0 for ticker in tickers)
            for ticker in tickers:
                if ticker_first:
                    if ticker not in level0:
                        continue
                    one = data[ticker].copy()
                else:
                    level1 = set(map(str, data.columns.get_level_values(1)))
                    if ticker not in level1:
                        continue
                    one = data.xs(ticker, axis=1, level=1).copy()
                frames.append(self._flatten_one_ticker(one, ticker))
        else:
            frames.append(self._flatten_one_ticker(data.copy(), tickers[0]))

        if not frames:
            return self.empty_history_frame()
        return pd.concat(frames, ignore_index=True)

    def _flatten_one_ticker(self, data: pd.DataFrame, ticker: str) -> pd.DataFrame:
        frame = data.reset_index()
        frame.columns = [_normalise_column_name(column) for column in frame.columns]
        if "date" not in frame.columns and "datetime" in frame.columns:
            frame = frame.rename(columns={"datetime": "date"})
        if "adj_close" not in frame.columns:
            frame["adj_close"] = pd.NA
        if "dividends" not in frame.columns:
            frame["dividends"] = pd.NA
        if "stock_splits" not in frame.columns:
            frame["stock_splits"] = pd.NA
        frame.insert(0, "ticker", ticker)
        columns = [
            "ticker",
            "date",
            "open",
            "high",
            "low",
            "close",
            "adj_close",
            "volume",
            "dividends",
            "stock_splits",
        ]
        for column in columns:
            if column not in frame.columns:
                frame[column] = pd.NA
        return frame.loc[:, columns]

    def empty_history_frame(self) -> pd.DataFrame:
        return pd.DataFrame(
            columns=[
                "ticker",
                "date",
                "open",
                "high",
                "low",
                "close",
                "adj_close",
                "volume",
                "dividends",
                "stock_splits",
            ]
        )


class YFinanceClient:
    """Reusable yfinance client facade for search, validation and history."""

    def __init__(self, session: Any = None, timeout: int | float = 10) -> None:
        self.search_client = YFinanceSearchClient(session=session, timeout=int(timeout))
        self.market_data_client = YFinanceMarketDataClient(session=session, timeout=timeout)

    def search(self, *args: Any, **kwargs: Any) -> pd.DataFrame:
        return self.search_client.search(*args, **kwargs)

    def lookup(self, *args: Any, **kwargs: Any) -> pd.DataFrame:
        return self.search_client.lookup(*args, **kwargs)

    def validate_tickers(self, *args: Any, **kwargs: Any) -> pd.DataFrame:
        return self.search_client.validate_tickers(*args, **kwargs)

    def download_history(self, *args: Any, **kwargs: Any) -> pd.DataFrame:
        return self.market_data_client.download_history(*args, **kwargs)
