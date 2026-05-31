from __future__ import annotations


from dataclasses import dataclass
from datetime import date, datetime

from quant_database.schema.base import SchemaModel, TableSchema



@dataclass
class MarketBarsDaily(SchemaModel):
    instrument_id: str
    trade_date: date
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    pre_close: float | None
    change: float | None
    pct_chg: float | None
    volume: float | None
    amount: float | None
    source_code: str
    source_id: str
    updated_at: datetime


@dataclass
class MarketBarsDerivativeDaily(SchemaModel):
    instrument_id: str
    trade_date: date
    pre_close: float | None
    pre_settle: float | None
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    settle: float | None
    change1: float | None
    change2: float | None
    vol: float | None
    amount: float | None
    oi: float | None
    oi_chg: float | None
    delv_settle: float | None
    source_code: str
    source_id: str
    updated_at: datetime




MARKET_BARS_DAILY = TableSchema(
    name="market_bars_daily",
    model=MarketBarsDaily,
    primary_key=("instrument_id", "trade_date"),
    description="市场日线行情表",
    indexes=(("instrument_id", "trade_date"), ("source_id", "trade_date")),
)

MARKET_BARS_DERIVATIVE_DAILY = TableSchema(
    name="market_bars_derivative_daily",
    model=MarketBarsDerivativeDaily,
    primary_key=("instrument_id", "trade_date"),
    description="市场期货日线行情表",
    indexes=(("instrument_id", "trade_date"), ("source_id", "trade_date")),
)



if __name__ == "__main__":
    print(MARKET_BARS_DAILY.create_table_sql())
