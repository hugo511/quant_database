from __future__ import annotations


from dataclasses import dataclass
from datetime import date, datetime

from quant_database.schema.base import SchemaModel, TableSchema


@dataclass
class ReferenceInstrument(SchemaModel):
    """
    股票基本信息表
        来自tushare: https://tushare.pro/document/2?doc_id=25
        接口：stock_basic，可以通过数据工具调试和查看数据
        描述：获取基础信息数据，包括股票代码、名称、上市日期、退市日期等
        限量：每次最多返回6000行数据（覆盖全市场A股，会随股票总数增长而增加）
        权限：2000积分起，每分钟请求50次。此接口是基础信息，调取一次就可以拉取完，建议保存倒本地存储后使用
    Args:
        instrument_id: 股票代码
        symbol: 股票代码
        name: 股票名称
        full_name: 全称
        enname: 英文名称
        market: 市场（主板/创业板/科创板/CDR）
        exchange: 交易所
        currency: 货币
        list_status: 上市状态, L上市 D退市 G过会未交易 P暂停上市
        list_date: 上市日期
        delist_date: 退市日期
        is_hs: 是否沪深港通标的, N否 H沪股通 S深股通
        source_code: 原始标的code 
        source_id: 数据来源
        asset_class: 资产大类, equity / fund / index / bond / future / option
        instrument_type: 标的类型, stock / etf / index / treasury_bond / convertible_bond
        index_code: ETF 跟踪指数代码
        exp_date: 指数终止日期
    """
    instrument_id: str
    symbol: str
    name: str
    full_name: str
    enname: str | None
    market: str | None
    exchange: str
    currency: str | None
    list_status: str | None
    list_date: date | None
    delist_date: date | None
    is_hs: bool
    source_code: str
    source_id: str
    asset_class: str
    instrument_type: str
    index_code: str | None
    exp_date: date | None


@dataclass
class InstrumentStockST(SchemaModel):
    """
    ST股票列表
        来自tushare: https://tushare.pro/document/2?doc_id=397
        描述：获取ST股票列表，可根据交易日期获取历史上每天的ST列表
        权限：3000积分起
        提示：每天上午9:20更新，单次请求最大返回1000行数据，可循环提取,本接口数据从20160101开始,太早历史无法补齐
    Args:
        instrument_id: 股票代码
        name: 股票名称
        trade_date: 交易日期
        type: 类型
        type_name: 类型名称
        source_code: 原始标的code 
        source_id: 数据来源
    """
    instrument_id: str
    name: str
    trade_date: date
    type: str
    type_name: str
    source_code: str
    source_id: str


@dataclass
class ReferenceFuture(SchemaModel):
    """期货合约信息表
        https://tushare.pro/document/2?doc_id=135
    Args:
        instrument_id: 合约代码
        symbol: 合约代码
        exchange: 交易所
        name: 合约名称
        type: 类型
        type_name: 类型名称
        list_date: 上市日期
        delist_date: 退市日期
        source_code: 原始标的code 
        source_id: 数据来源
    """
    instrument_id: str
    symbol: str
    exchange: str
    name: str
    fut_code: str | None
    multiplier: float | None
    trade_unit: str | None
    per_unit: float | None
    quote_unit: str | None
    quote_unit_desc: str | None
    d_mode_desc: str | None
    list_date: date | None
    delist_date: date | None
    d_month: str | None
    last_ddate: date | None
    source_code: str
    source_id: str
    updated_at: datetime

    



REFERENCE_INSTRUMENT = TableSchema(
    name="reference_instrument",
    model=ReferenceInstrument,
    primary_key=("instrument_id",),
    description="股票基本信息表",
    indexes=(("instrument_id",), ("market", "exchange")),
)


INSTRUMENT_STOCK_ST = TableSchema(
    name="reference_instrument_st",
    model=InstrumentStockST,
    primary_key=("instrument_id", "trade_date", "type"),
    description="ST股票列表",
    indexes=(("instrument_id", "trade_date"),),
)

REFERENCE_FUTURE = TableSchema(
    name="reference_future",
    model=ReferenceFuture,
    primary_key=("instrument_id",),
    description="期货合约信息表",
    indexes=(("instrument_id",),),
)



if __name__ == "__main__":
    print(REFERENCE_INSTRUMENT.create_table_sql())
    print(INSTRUMENT_STOCK_ST.create_table_sql())
