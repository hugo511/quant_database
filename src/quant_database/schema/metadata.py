from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from quant_database.schema.base import TableSchema


@dataclass
class UpdateRun:
    run_id: str
    run_env: str
    config_path: str
    database_path: str
    started_at: datetime
    finished_at: datetime | None
    status: str
    message: str | None


@dataclass
class UpdateEvent:
    event_id: str
    run_id: str
    source_id: str
    table_name: str
    scope: str
    started_at: datetime
    finished_at: datetime | None
    status: str
    rows_read: int
    rows_written: int
    message: str | None


@dataclass
class SyncState:
    source_id: str
    table_name: str
    scope: str
    last_success_date: date | None
    last_success_at: datetime | None
    status: str
    message: str | None
    updated_at: datetime


UPDATE_RUNS = TableSchema(
    name="update_runs",
    model=UpdateRun,
    primary_key=("run_id",),
    description="一次完整运行的元数据，对应一个 YAML 配置和一个数据库产物",
    indexes=(("run_env",), ("status",), ("started_at",)),
)

UPDATE_EVENTS = TableSchema(
    name="update_events",
    model=UpdateEvent,
    primary_key=("event_id",),
    description="运行内每个数据集/范围的更新事件明细",
    indexes=(("run_id",), ("source_id", "table_name"), ("status",)),
)

SYNC_STATE = TableSchema(
    name="sync_state",
    model=SyncState,
    primary_key=("source_id", "table_name", "scope"),
    description="每个数据源、表、范围的增量同步状态",
    indexes=(("status",), ("updated_at",)),
)
