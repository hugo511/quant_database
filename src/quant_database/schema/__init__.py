from __future__ import annotations

from quant_database.schema.base import TableSchema
from quant_database.schema.market import (
    MARKET_BARS_DAILY,
    MARKET_BARS_DERIVATIVE_DAILY,
    MARKET_FX_DAILY,
    MarketBarsDaily,
    MarketBarsDerivativeDaily,
    MarketFXDaily,
)
from quant_database.schema.metadata import (
    SYNC_STATE,
    UPDATE_EVENTS,
    UPDATE_RUNS,
    SyncState,
    UpdateEvent,
    UpdateRun,
)
from quant_database.schema.reference import (
    INSTRUMENT_STOCK_ST,
    InstrumentStockST,
    REFERENCE_FUTURE,
    REFERENCE_INSTRUMENT,
    ReferenceFuture,
    ReferenceInstrument,
)

SCHEMAS: tuple[TableSchema, ...] = (
    REFERENCE_INSTRUMENT,
    INSTRUMENT_STOCK_ST,
    REFERENCE_FUTURE,
    MARKET_BARS_DAILY,
    MARKET_BARS_DERIVATIVE_DAILY,
    MARKET_FX_DAILY,
    UPDATE_RUNS,
    UPDATE_EVENTS,
    SYNC_STATE,
)

__all__ = [
    "INSTRUMENT_STOCK_ST",
    "MARKET_BARS_DAILY",
    "MARKET_BARS_DERIVATIVE_DAILY",
    "MARKET_FX_DAILY",
    "REFERENCE_FUTURE",
    "REFERENCE_INSTRUMENT",
    "SCHEMAS",
    "SYNC_STATE",
    "UPDATE_EVENTS",
    "UPDATE_RUNS",
    "InstrumentStockST",
    "MarketBarsDaily",
    "MarketBarsDerivativeDaily",
    "MarketFXDaily",
    "ReferenceFuture",
    "ReferenceInstrument",
    "SyncState",
    "TableSchema",
    "UpdateEvent",
    "UpdateRun",
]
