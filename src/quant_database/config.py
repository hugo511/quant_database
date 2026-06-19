from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from quant_database.providers.tushare_client import find_project_root
from utils.tools import DateParser


DATASETS_WITH_DATE_RANGE = {
    "stock_st",
    "stock_daily",
    "market_bars_etf_daily",
    "market_bars_index_daily",
    "market_bars_future_daily",
    "market_fx_daily",
    "yf_market_bars_daily",
}


@dataclass(frozen=True)
class DateRangeConfig:
    start: Any
    end: Any

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DateRangeConfig:
        if "start" not in data or "end" not in data:
            raise ValueError("date_range requires both `start` and `end`.")
        return cls(
            start=DateParser.to_date(data["start"]),
            end=DateParser.to_date(data["end"]),
        )

    def apply_defaults(self, params: dict[str, Any]) -> dict[str, Any]:
        merged = dict(params)
        merged.setdefault("start", self.start)
        merged.setdefault("end", self.end)
        return merged


@dataclass(frozen=True)
class DatasetRunConfig:
    name: str
    source: str = "tushare"
    enabled: bool = True
    params: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DatasetRunConfig:
        return cls(
            name=data["name"],
            source=str(data.get("source", "tushare")),
            enabled=bool(data.get("enabled", True)),
            params=dict(data.get("params", {})),
        )


@dataclass(frozen=True)
class TushareRunConfig:
    run_env: str = "test"
    date_range: DateRangeConfig | None = None
    root_dir: str | None = None
    db_path: str | None = None
    datasets: list[DatasetRunConfig] = field(default_factory=list)

    @classmethod
    def from_yaml(cls, path: str | Path) -> TushareRunConfig:
        config_path = Path(path).expanduser().resolve()
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        return cls.from_dict(data, config_path=config_path)

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
        config_path: Path | None = None,
    ) -> TushareRunConfig:
        date_range = (
            DateRangeConfig.from_dict(data["date_range"])
            if data.get("date_range") is not None
            else None
        )
        datasets = [
            cls._dataset_from_dict(item, date_range)
            for item in data.get("datasets", [])
        ]
        config = cls(
            run_env=data.get("run_env", "test"),
            date_range=date_range,
            root_dir=data.get("root_dir"),
            db_path=data.get("db_path"),
            datasets=datasets,
        )
        if not config.datasets:
            raise ValueError("Config requires at least one dataset.")
        return config

    @staticmethod
    def _dataset_from_dict(
        data: dict[str, Any],
        date_range: DateRangeConfig | None,
    ) -> DatasetRunConfig:
        dataset = DatasetRunConfig.from_dict(data)
        if date_range is None or dataset.name not in DATASETS_WITH_DATE_RANGE:
            return dataset
        return DatasetRunConfig(
            name=dataset.name,
            source=dataset.source,
            enabled=dataset.enabled,
            params=date_range.apply_defaults(dataset.params),
        )

    def resolved_root_dir(self, config_path: str | Path | None = None) -> Path:
        project_root = find_project_root()
        if self.root_dir:
            root = Path(self.root_dir).expanduser()
            return root if root.is_absolute() else project_root / root

        base = "test_data" if self.run_env == "test" else "data"
        return project_root / base

    def resolved_db_path(self, root_dir: Path) -> Path | None:
        if not self.db_path:
            return None
        path = Path(self.db_path).expanduser()
        return path if path.is_absolute() else root_dir / path

    def datasets_for_source(self, source: str) -> list[DatasetRunConfig]:
        return [dataset for dataset in self.datasets if dataset.source == source]
