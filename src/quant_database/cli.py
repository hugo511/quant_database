from __future__ import annotations

import sys
from pathlib import Path

from quant_database.config import TushareRunConfig
from quant_database.loaders.tushare.loader import LoadResult, TushareLoader
from quant_database.loaders.yfinance.loader import YFinanceLoader
from quant_database.providers.tushare_client import find_project_root
from utils.logger import logger


def _log_result(result: LoadResult) -> None:
    logger.info(
        f"{result.dataset}: raw_rows={result.raw_rows}, "
        f"rows_written={result.rows_written}, "
        f"raw={display_path(result.raw_path)}, db={display_path(result.db_path)}"
    )


def display_path(path: str | Path) -> Path:
    resolved = Path(path).expanduser().resolve()
    project_root = find_project_root()
    try:
        return resolved.relative_to(project_root)
    except ValueError:
        return resolved


def run_tushare_config(config_path: str | Path) -> list[LoadResult]:
    config = TushareRunConfig.from_yaml(config_path)
    root_dir = config.resolved_root_dir(config_path)
    db_path = config.resolved_db_path(root_dir)
    loaders = {
        "tushare": TushareLoader(root_dir=root_dir, db_path=db_path),
        "yfinance": YFinanceLoader(root_dir=root_dir, db_path=db_path),
    }

    results: list[LoadResult] = []
    for dataset in config.datasets:
        if not dataset.enabled:
            continue
        if dataset.source not in loaders:
            logger.info(f"Skip dataset {dataset.name}: source={dataset.source} is not supported.")
            continue
        result = loaders[dataset.source].update(dataset.name, **dataset.params)
        results.append(result)
        _log_result(result)

    return results


def print_results(results: list[LoadResult]) -> None:
    for result in results:
        _log_result(result)


def main(config_path: str | Path | None = None) -> None:
    if config_path is None:
        if len(sys.argv) != 2:
            raise SystemExit("Usage: python -m quant_database.cli path/to/run.yaml")
        config_path = sys.argv[1]
    run_tushare_config(config_path)


if __name__ == "__main__":
    main()
