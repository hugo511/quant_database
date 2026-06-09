from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from quant_database.config import TushareRunConfig
from quant_database.providers.yfinance_client import YFinanceClient
from utils.logger import logger


DEFAULT_CONFIG = Path(__file__).with_name("one_day.yaml")


def main(config_path: str | Path = DEFAULT_CONFIG) -> None:
    config = TushareRunConfig.from_yaml(config_path)
    client = YFinanceClient()

    for dataset in config.datasets:
        if not dataset.enabled or dataset.source != "yfinance":
            continue
        if dataset.name != "history":
            logger.info(f"Skip yfinance dataset {dataset.name}: no runner handler.")
            continue

        frame = client.download_history(**dataset.params)
        logger.info(f"yfinance history rows={len(frame)}, columns={list(frame.columns)}")
        if not frame.empty:
            logger.info(f"\n{frame.head().to_string(index=False)}")


if __name__ == "__main__":
    config_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CONFIG
    main(config_path)
