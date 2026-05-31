from __future__ import annotations

from pathlib import Path

import duckdb


DEFAULT_DB_PATH = Path("data") / "quant.duckdb"


def resolve_db_path(path: str | Path | None = None) -> Path:
    return Path(path or DEFAULT_DB_PATH).expanduser().resolve()


def connect(path: str | Path | None = None) -> duckdb.DuckDBPyConnection:
    db_path = resolve_db_path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(db_path))
