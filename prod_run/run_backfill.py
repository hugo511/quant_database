from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

os.environ["LOG_DIR"] = str(ROOT / "prod_logs")

from quant_database.cli import main


DEFAULT_CONFIG = Path(__file__).with_name("backfill.yaml")


if __name__ == "__main__":
    config_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CONFIG
    main(config_path)
