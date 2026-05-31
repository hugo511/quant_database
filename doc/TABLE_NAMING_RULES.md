# 量化数据库表命名规则

## 一、命名格式
{domain}{table_type}{granularity}_{suffix}


### 各层说明

| 层级 | 说明 | 可选值 |
|------|------|--------|
| **domain** | 数据域 | `market`, `reference`, `fundamental`, `factor` |
| **table_type** | 表类型 | `bars`, `info`, `components`, `weights`, `financial`, `value` |
| **granularity** | 频率/粒度 | `daily`, `minute_1`, `minute_5`, `quarterly`, `annual` |
| **suffix** | 可选修饰 | `stock`, `etf`, `index`, `cn`（中国特定） |

### 示例
market_bars_daily # 日线行情
reference_instruments_stock # 股票标的表
factor_value_daily # 日频因子值
fundamental_financial_quarterly # 季报财务数据



---

## 二、Domain（数据域）定义

| Domain | 含义 | 说明 |
|--------|------|------|
| `market` | 行情数据 | OHLCV、Tick、快照等时间序列 |
| `reference` | 参考数据 | 标的信息、成分股、权重、行业分类等静态/低频变更信息 |
| `fundamental` | 基本面数据 | 财务三表、财务指标、分红、股本变动 |
| `factor` | 因子数据 | 因子值、因子收益、因子暴露 |
| `portfolio` | 组合数据 | 持仓、交易记录、绩效归因（可选） |

---

## 三、核心表命名规范

### 1. 行情数据表（market）

| 表名 | 粒度 | 主键示例 | 说明 |
|------|------|---------|------|
| `market_bars_daily` | 日 | `instrument_id, trade_date` | 统一日线行情（股票/ETF/指数） |
| `market_bars_minute_1` | 1分钟 | `instrument_id, timestamp` | 1分钟线 |
| `market_bars_minute_5` | 5分钟 | `instrument_id, timestamp` | 5分钟线 |
| `market_quotes_snapshot` | 快照 | `instrument_id, trade_date` | 盘后快照/实时快照 |
| `market_tick` | 逐笔 | `instrument_id, trade_time` | Tick 数据（体积大，慎用） |

### 2. 参考数据表（reference）

| 表名 | 含义 | 主键示例 | 更新频率 |
|------|------|---------|---------|
| `reference_instruments` | 统一标的表 | `instrument_id` | 低频（日/周） |
| `reference_instruments_stock` | 股票信息 | `instrument_id` | 低频 |
| `reference_instruments_etf` | ETF 信息 | `instrument_id` | 低频 |
| `reference_instruments_index` | 指数信息 | `instrument_id` | 低频 |
| `reference_index_components` | 指数成分股 | `index_id, instrument_id, trade_date` | 定期更新 |
| `reference_index_weights` | 指数权重 | `index_id, instrument_id, trade_date` | 定期更新 |
| `reference_sector` | 板块/行业分类 | `sector_id, instrument_id` | 低频 |
| `reference_exchange` | 交易所信息 | `exchange_id` | 静态 |
| `reference_calendar` | 交易日历 | `trade_date` | 静态/年更新 |

### 3. 基本面数据表（fundamental）

| 表名 | 含义 | 主键示例 | 频率 |
|------|------|---------|------|
| `fundamental_financial_quarterly` | 财务三表（季报） | `instrument_id, report_date` | 季频 |
| `fundamental_financial_annual` | 财务三表（年报） | `instrument_id, report_date` | 年频 |
| `fundamental_indicators_quarterly` | 财务指标（ROE/PE/PB） | `instrument_id, report_date` | 季频 |
| `fundamental_dividend` | 分红配送 | `instrument_id, ex_date` | 低频 |
| `fundamental_share_change` | 股本变动 | `instrument_id, change_date` | 低频 |

### 4. 因子数据表（factor）

| 表名 | 粒度 | 主键示例 | 说明 |
|------|------|---------|------|
| `factor_value_daily` | 日 | `factor_name, instrument_id, trade_date` | 日频因子值 |
| `factor_value_weekly` | 周 | `factor_name, instrument_id, trade_date` | 周频因子值 |
| `factor_return_daily` | 日 | `factor_name, trade_date` | 因子每日收益 |
| `factor_exposure` | 截面 | `factor_name, instrument_id, trade_date` | 因子暴露矩阵 |

---

## 四、命名原则

| 原则 | 正确示例 | 错误示例 |
|------|---------|---------|
| **小写 + 下划线** | `market_bars_daily` | `MarketBarsDaily` / `marketBarsDaily` |
| **域在前，类型在中，粒度在后** | `fundamental_indicators_quarterly` | `indicators_fundamental` |
| **单数表名** | `reference_instruments` | `reference_instrument` |
| **避免模糊缩写** | `market_bars_daily` | `mkt_bar_d` |
| **时间字段统一** | `trade_date` / `timestamp` | `date1` / `dt` |
| **前后一致** | 全库用 `instrument_id` | 混合 `symbol` / `code` / `sec_id` |

---

## 五、DuckDB Schema 组织（可选）

```sql
CREATE SCHEMA IF NOT EXISTS market;
CREATE SCHEMA IF NOT EXISTS reference;
CREATE SCHEMA IF NOT EXISTS fundamental;
CREATE SCHEMA IF NOT EXISTS factor;