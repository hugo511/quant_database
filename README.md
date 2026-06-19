# QuantDatabase

QuantDatabase 是一个面向个人量化研究与数据工程实践的本地金融数据库项目。项目使用 DuckDB 作为本地分析型数据库，聚合 Tushare、yfinance 等数据源，将不同来源、不同资产类型的数据统一到一套清晰、可增量更新、可补数、可扩展的本地 schema 中。

当前项目已经形成基础闭环：

```text
数据源 API -> raw parquet 留痕 -> Polars 字段映射/类型转换 -> DuckDB upsert
```

## 设计目标

- **本地优先**：数据库以本地 DuckDB 文件保存，适合个人研究、回测和数据核查。
- **原始留痕**：API 返回的原始数据保存为 parquet，方便排错、对账和追溯。
- **统一 schema**：内部表结构不直接复制外部 API，而是承接多数据源、多资产类型。
- **增量更新**：以 DuckDB 已有数据为准，按日期区间和标的状态计算需要更新的缺失区间。
- **生产与补数分离**：每日生产更新和历史补数使用不同 YAML 与入口脚本。
- **配置驱动运行**：运行参数放在 YAML 中，脚本只负责读取配置并执行。
- **dataclass 驱动表结构**：schema 使用 Python `dataclass` 定义字段、类型和 DuckDB DDL。

## 当前能力

### Tushare

| Dataset | Raw parquet | DuckDB 表 | 说明 |
| --- | --- | --- | --- |
| `stock_list` | `raw/tushare/reference_instrument.parquet` | `reference_instrument` | A 股股票标的信息 |
| `stock_st` | `raw/tushare/reference_instrument_st.parquet` | `reference_instrument_st` | ST 股票列表 |
| `stock_daily` | `raw/tushare/stock_daily.parquet` | `market_bars_daily` | A 股日线行情 |
| `etf_list` | `raw/tushare/reference_instrument.parquet` | `reference_instrument` | ETF 标的信息 |
| `market_bars_etf_daily` | `raw/tushare/fund_daily.parquet` | `market_bars_daily` | ETF 日线行情 |
| `index_list` | `raw/tushare/reference_instrument.parquet` | `reference_instrument` | 指数标的信息 |
| `market_bars_index_daily` | `raw/tushare/index_daily.parquet` | `market_bars_daily` | A 股指数、国际指数、南华指数行情 |
| `future_list` | `raw/tushare/future_basic.parquet` | `reference_instrument`, `reference_future` | 期货合约基础信息 |
| `market_bars_future_daily` | `raw/tushare/future_daily.parquet` | `market_bars_derivative_daily` | 期货日线行情 |
| `market_fx_daily` | `raw/tushare/fx_daily.parquet` | `reference_instrument`, `market_fx_daily` | 外汇双边报价行情 |

`market_bars_index_daily` 通过 YAML 参数 `api` 支持不同 Tushare 指数接口：

```yaml
api: index_daily      # A 股指数日线
api: index_global     # 国际主要指数
api: fut_index_daily  # 南华期货指数
```

### yfinance

| Dataset | Raw parquet | DuckDB 表 | 说明 |
| --- | --- | --- | --- |
| `yf_instrument` | `raw/yfinance/reference_instrument.parquet` | `reference_instrument` | yfinance ticker 验证与标的信息 |
| `yf_market_bars_daily` | `raw/yfinance/daily.parquet` | `market_bars_daily` | yfinance 日线行情 |

## 目录结构

```text
quant_database/
├── README.md
├── pyproject.toml
├── main.py                         # 默认读取 test_run/one_day.yaml
├── .env.example                    # Tushare token 示例
│
├── src/
│   ├── quant_database/
│   │   ├── config.py               # YAML 运行配置 dataclass
│   │   ├── cli.py                  # 配置驱动运行入口
│   │   │
│   │   ├── schema/
│   │   │   ├── base.py             # SchemaModel / TableSchema / DDL 生成
│   │   │   ├── reference.py        # 标的、ST、期货合约等参考数据表
│   │   │   ├── market.py           # 行情表
│   │   │   └── metadata.py         # update_runs / update_events / sync_state
│   │   │
│   │   ├── core/
│   │   │   ├── connection.py       # DuckDB 连接
│   │   │   ├── bootstrap.py        # 建表、补充缺失列、创建索引
│   │   │   └── upsert.py           # 按主键删除后插入
│   │   │
│   │   ├── providers/
│   │   │   ├── tushare_client.py   # Tushare API 封装
│   │   │   └── yfinance_client.py  # yfinance API 封装
│   │   │
│   │   └── loaders/
│   │       ├── tushare/
│   │       │   └── loader.py       # Tushare 数据集下载、转换、入库
│   │       └── yfinance/
│   │           └── loader.py       # yfinance 数据集下载、转换、入库
│   │
│   └── utils/
│       ├── logger.py               # loguru 日志配置
│       └── tools.py                # 日期工具、parquet 更新、DuckDB 状态查询
│
├── test_run/
│   ├── one_day.yaml                # 测试运行配置
│   └── run_one_day.py              # 测试运行入口
│
├── prod_run/
│   ├── prod.yaml                   # 每日生产更新配置
│   ├── run_prod.py                 # 每日生产运行入口
│   ├── backfill.yaml               # 历史补数配置
│   └── run_backfill.py             # 历史补数运行入口
│
├── test_data/                      # 测试 DuckDB 与 raw parquet，本地生成
├── prod_data/                      # 生产 DuckDB 与 raw parquet，本地生成
├── test_logs/                      # 测试运行日志，本地生成
├── prod_logs/                      # 生产运行日志，本地生成
└── doc/                            # 设计文档
```

## 快速开始

### 1. 安装依赖

项目使用 Python 3.12 和 uv 管理依赖：

```bash
uv sync
```

### 2. 配置 Tushare Token

复制 `.env.example` 为 `.env`，并填入 Tushare token：

```text
TUSHARE_TOKEN=your_tushare_token_here
```

`TushareClient` 会优先读取环境变量 `TUSHARE_TOKEN`，也会读取项目根目录 `.env` 中的同名配置。

### 3. 测试运行

测试配置文件：

```text
test_run/one_day.yaml
```

运行：

```bash
uv run python test_run/run_one_day.py
```

或通过 `main.py`：

```bash
uv run python main.py
```

### 4. 生产每日更新

生产配置文件：

```text
prod_run/prod.yaml
```

运行：

```bash
uv run python prod_run/run_prod.py
```

每日生产配置通常使用：

```yaml
date_range:
  start: 2026-06-18
  end: today
```

`today` 会在程序运行时解析为当天日期。

### 5. 生产补数

补数配置文件：

```text
prod_run/backfill.yaml
```

运行：

```bash
uv run python prod_run/run_backfill.py
```

补数配置建议使用明确日期，保证可复现：

```yaml
date_range:
  start: 2026-06-01
  end: 2026-06-18
```

## YAML 配置说明

运行配置由 YAML 管理：

```yaml
run_env: prod
root_dir: prod_data
date_range:
  start: 2026-06-18
  end: today

datasets:
  - source: tushare
    name: stock_daily
    enabled: true
    params: {}
```

核心字段：

- `run_env`：运行环境标识，常用 `test` 或 `prod`。
- `root_dir`：本次运行的数据根目录，例如 `test_data`、`prod_data`。
- `date_range`：全局业务日期区间。
- `datasets`：本次运行的数据集列表。
- `source`：数据源，例如 `tushare`、`yfinance`。
- `name`：dataset 名称。
- `enabled`：是否运行该 dataset。
- `params`：传给 dataset 的业务参数。

顶层 `date_range` 会自动注入给需要日期窗口的 dataset。单个 dataset 也可以在 `params` 中覆盖：

```yaml
date_range:
  start: 2026-06-18
  end: today

datasets:
  - source: tushare
    name: market_bars_index_daily
    enabled: true
    params:
      api: index_global
      start: 2026-06-01
      end: 2026-06-18
      index_codes:
        - HKTECH
```

## 数据落盘结构

测试运行写入：

```text
test_data/
├── quant.duckdb
└── raw/
    ├── tushare/
    └── yfinance/
```

生产运行写入：

```text
prod_data/
├── quant.duckdb
└── raw/
    ├── tushare/
    └── yfinance/
```

raw parquet 只保存 API 原始返回数据，用于核查和追溯；增量更新状态以 DuckDB 业务表为准。

## 日志

日志使用 `src/utils/logger.py` 中的 loguru 配置。

不同入口会设置不同日志目录：

```text
test_run/run_one_day.py  -> test_logs/
prod_run/run_prod.py     -> prod_logs/
prod_run/run_backfill.py -> prod_logs/
```

日志文件按级别保存：

```text
info.log
warning.log
error.log
```

例如生产运行主要查看：

```text
prod_logs/info.log
```

## 数据模型

### Reference 表

`reference_instrument` 是统一标的主表，用于保存股票、ETF、指数、期货、外汇等资产的通用字段：

- `instrument_id`
- `symbol`
- `name`
- `full_name`
- `market`
- `exchange`
- `currency`
- `list_status`
- `list_date`
- `delist_date`
- `source_code`
- `source_id`
- `asset_class`
- `instrument_type`

资产专属字段使用扩展表保存。例如期货合约专属字段保存在 `reference_future`，避免把所有资产类型字段堆进统一标的表。

### Market 表

`market_bars_daily` 保存股票、ETF、指数等通用日线行情：

- `instrument_id`
- `trade_date`
- `open/high/low/close`
- `pre_close`
- `change`
- `pct_chg`
- `volume`
- `amount`
- `source_code`
- `source_id`
- `updated_at`

`market_bars_derivative_daily` 保存期货等衍生品行情：

- `pre_close`
- `pre_settle`
- `open/high/low/close`
- `settle`
- `change1`
- `change2`
- `vol`
- `amount`
- `oi`
- `oi_chg`
- `delv_settle`

`market_fx_daily` 保存外汇双边报价：

- `bid_open/high/low/close`
- `ask_open/high/low/close`
- `mid_open/high/low/close`
- `spread_close`

## 增量与补数逻辑

核心原则：

```text
以 DuckDB 业务表中已写入的数据为准，不以 raw parquet 为准。
```

当前主要逻辑：

- `stock_daily`：按全表日期缺口更新，Tushare 可按交易日获取全市场股票日线。
- `market_bars_etf_daily`：从 `reference_instrument` 获取 ETF 池，批量查询各 ETF 已有日期范围，再按缺失区间逐标的拉取。
- `market_bars_index_daily`：按配置中的指数代码或 reference 中筛选出的指数逐标的更新。
- `market_bars_future_daily`：从 `reference_future` 获取有效合约，批量查询各合约已有日期范围，再按缺失区间逐合约拉取。
- `market_fx_daily`：按 YAML 中的 `ts_codes` 更新外汇对，并自动写入最小 `reference_instrument` 记录。
- `yf_market_bars_daily`：按输入区间下载 yfinance 行情，写入前过滤 DuckDB 已存在的 `(instrument_id, trade_date)`。

缺失区间支持向前补数。例如数据库已有：

```text
2026-06-01 ~ 2026-06-18
```

补数配置为：

```text
2020-01-01 ~ 2026-05-31
```

程序会识别并拉取前段缺失区间。

注意：当前缺失区间主要基于每个标的的 `min(trade_date)` 和 `max(trade_date)` 判断，适合识别前段和后段缺失；如果中间日期断档，后续可以增加更细粒度的缺口扫描。

## Metadata 表

项目已定义运行元数据表：

- `update_runs`
- `update_events`
- `sync_state`

这些表用于后续记录运行批次、单个 dataset 更新事件和同步状态。当前增量更新主要以业务表中的日期范围为准，metadata 表尚未作为主增量状态来源。

## 路线图

近期方向：

- 优化期货日线每日模式下的有效合约筛选。
- 逐步统一 Index / FX 的批量更新状态查询。
- 完善运行记录表 `update_runs` / `update_events` 的写入逻辑。
- 增加数据质量检查、缺口扫描和异常提示。

长期方向：

- 统一多数据源标的映射和数据优先级。
- 扩展基本面、分钟行情、因子数据表。
- 支持更完整的数据版本管理和自动补数流程。
