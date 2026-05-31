from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from quant_database.providers.tushare_client import find_project_root


@dataclass(frozen=True)
class DatasetRunConfig:
    name: str
    enabled: bool = True
    params: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DatasetRunConfig:
        return cls(
            name=data["name"],
            enabled=bool(data.get("enabled", True)),
            params=dict(data.get("params", {})),
        )


@dataclass(frozen=True)
class TushareRunConfig:
    run_env: str = "test"
    run_date: str | None = None
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
        datasets = [
            DatasetRunConfig.from_dict(item)
            for item in data.get("datasets", [])
        ]
        config = cls(
            run_env=data.get("run_env", "test"),
            run_date=str(data["run_date"]) if data.get("run_date") is not None else None,
            root_dir=data.get("root_dir"),
            db_path=data.get("db_path"),
            datasets=datasets,
        )
        if not config.datasets:
            raise ValueError("Config requires at least one dataset.")
        return config

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
