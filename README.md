# QuantDatabase

QuantDatabase 是一个面向个人量化研究与数据工程实践的本地金融数据库项目。项目目标是用 DuckDB 作为本地分析型数据库，聚合 Tushare、yfinance 等数据源，将不同来源、不同资产类型的数据统一到一套清晰、可增量更新、可扩展的本地 schema 中。

当前阶段项目重点先打通 Tushare 数据源的最小闭环：

```text
Tushare API -> raw parquet 留痕 -> Polars 字段映射/类型转换 -> DuckDB upsert
```

## 设计目标

- **本地优先**：数据库以单个 DuckDB 文件保存，适合个人研究、回测和跨设备迁移。
- **增量更新**：以 DuckDB 中已有数据状态为准，计算需要补充的日期或标的区间。
- **原始数据留痕**：下载得到的原始数据保存为 parquet，便于核查、回放和排错。
- **统一 schema**：用项目内部表结构承接不同数据源，而不是直接复制外部 API 表。
- **dataclass 驱动表结构**：Python `dataclass` 是 DuckDB DDL(Data Definition Language)、字段顺序和类型转换的代码定义源。
- **YAML 编排运行参数**：测试和生产运行参数放在 YAML 中，脚本只负责读取配置并执行。

## 当前能力

已实现的 Tushare 数据集：

| Dataset | Raw parquet | DuckDB 表 | 说明 |
| --- | --- | --- | --- |
| `stock_list` | `raw/tushare/reference_instrument.parquet` | `reference_instrument` | A 股股票标的信息 |
| `stock_st` | `raw/tushare/reference_instrument_st.parquet` | `reference_instrument_st` | ST 股票列表 |
| `stock_daily` | `raw/tushare/stock_daily.parquet` | `market_bars_daily` | A 股日线行情 |
| `etf_list` | `raw/tushare/reference_instrument.parquet` | `reference_instrument` | ETF 标的信息 |
| `market_bars_etf_daily` | `raw/tushare/fund_daily.parquet` | `market_bars_daily` | ETF 日线行情 |
| `index_list` | `raw/tushare/reference_instrument.parquet` | `reference_instrument` | 指数标的信息 |
| `market_bars_index_daily` | `raw/tushare/index_daily.parquet` | `market_bars_daily` | 指数日线行情 |
| `future_list` | `raw/tushare/future_basic.parquet` | `reference_instrument`, `reference_future` | 期货合约基础信息 |
| `market_bars_future_daily` | `raw/tushare/future_daily.parquet` | `market_bars_derivative_daily` | 期货日线行情 |

## 目录结构

```text
quant_database/
├── README.md
├── pyproject.toml
├── main.py                         # 默认读取 test_run/tushare_one_day.yaml
├── .env.example                    # Tushare token 示例
│
├── src/
│   ├── quant_database/
│   │   ├── config.py               # YAML 运行配置 dataclass
│   │   ├── cli.py                  # 配置驱动的运行入口
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
│   │   │   └── tushare_client.py   # Tushare API 封装
│   │   │
│   │   └── loaders/
│   │       └── tushare/
│   │           └── loader.py       # Tushare 数据集下载、转换、入库
│   │
│   └── utils/
│       ├── logger.py
│       └── tools.py                # 日期工具、parquet 增量更新、DuckDB 状态查询
│
├── test_run/
│   ├── tushare_one_day.yaml        # 测试运行参数
│   └── run_tushare_one_day.py      # 测试运行入口
│
├── test_data/                      # 测试数据库和 raw parquet，运行后生成
├── data/                           # 生产数据库和 raw parquet，运行后生成
└── doc/                            # 设计文档
```

## 快速开始

### 1. 安装依赖

项目使用 Python 3.12 和 uv 管理依赖：

```bash
uv sync
```

### 2. 配置 Tushare Token

复制 `.env.example` 为 `.env`，并填入你的 Tushare token：

```text
TUSHARE_TOKEN=your_tushare_token_here
```

`TushareClient` 会优先读取环境变量 `TUSHARE_TOKEN`，也会读取项目根目录 `.env` 中的同名配置。

### 3. 修改运行 YAML

测试配置文件位于：

```text
test_run/tushare_one_day.yaml
```

示例：

```yaml
run_env: test
run_date: 2026-05-27
root_dir: test_data

datasets:
  - name: future_list
    enabled: true
    params:
      exchanges:
        - CFFEX
        - DCE
        - CZCE
        - SHFE
        - INE
        - GFEX

  - name: market_bars_future_daily
    enabled: false
    params:
      start: 2026-05-27
      end: 2026-05-27
```

### 4. 运行

使用默认测试配置：

```bash
uv run python main.py
```

或指定配置文件：

```bash
uv run python main.py test_run/tushare_one_day.yaml
```

也可以直接运行测试脚本：

```bash
uv run python test_run/run_tushare_one_day.py
```

运行结果默认写入：

```text
test_data/
├── quant.duckdb
└── raw/
    └── tushare/
        ├── reference_instrument.parquet
        ├── stock_daily.parquet
        ├── fund_daily.parquet
        ├── index_daily.parquet
        ├── future_basic.parquet
        └── future_daily.parquet
```

## 数据模型

### Reference 表

`reference_instrument` 是统一标的主表，用于保存股票、ETF、指数、期货等资产的通用字段：

- `instrument_id`
- `symbol`
- `name`
- `exchange`
- `currency`
- `list_date`
- `delist_date`
- `source_code`
- `source_id`
- `asset_class`
- `instrument_type`

对于资产专属字段，使用扩展表保存。例如期货合约专属字段保存在 `reference_future`，避免把所有资产类型的字段都堆进统一标的表。

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

期货、期权这类衍生品行情字段与普通 OHLCV 有差异，因此使用 `market_bars_derivative_daily` 保存结算价、持仓量等衍生品专属字段：

- `pre_settle`
- `settle`
- `change1`
- `change2`
- `vol`
- `oi`
- `oi_chg`
- `delv_settle`

### Metadata 表（后续完善）

项目已定义运行元数据表：

- `update_runs`
- `update_events`
- `sync_state`

这些表用于后续记录运行批次、单个 dataset 更新事件和同步状态。当前增量更新主要以业务表中的最新日期为准。

## 增量更新逻辑

当前核心原则是：**以 DuckDB 中已经写入的数据为准，而不是以 raw parquet 为准**。

- 股票日线：按 `trade_date` 缺口增量拉取。
- ETF / 指数日线：先从 `reference_instrument` 获取标的池，再按每个 `instrument_id` 查询最新 `trade_date` 后逐标的更新。
- 期货日线：先从 `reference_future` 获取更新区间内有效合约，再按每个合约最新 `trade_date` 逐合约更新。
- raw parquet 只保存原始 API 返回数据，用于核查和追溯。

## 当前阶段与路线图

当前阶段：Tushare 本地数据库最小闭环。

近期计划：

- 增加生产配置目录 `prod_run/` 的固定运行模板。
- 逐步接入 yfinance 等海外数据源。

长期方向：

- 统一多数据源标的映射和数据优先级。
- 构建基本面、日频行情、因子数据表。
- 支持更完整的数据质量检查、缺口扫描和自动补数。
