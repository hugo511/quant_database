from __future__ import annotations

import os
import time
from datetime import date, datetime
from tkinter import NO
from tracemalloc import start
from typing import Literal
from pathlib import Path
import functools

import pandas as pd
import requests
import tushare as ts


def find_project_root(start: Path | None = None) -> Path:
    """Find the repository root by walking upward to pyproject.toml."""
    current = (start or Path(__file__)).resolve()
    if current.is_file():
        current = current.parent

    for path in (current, *current.parents):
        if (path / "pyproject.toml").exists():
            return path

    return Path.cwd().resolve()


def load_env_file(env_path: str | Path) -> dict[str, str]:
    env: dict[str, str] = {}
    env_path = Path(env_path).expanduser().resolve()
    if not env_path.exists():
        return env

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip("'").strip('"')
    return env


def _fields(fields: list[str] | tuple[str, ...] | str | None) -> str | None:
    if fields is None or isinstance(fields, str):
        return fields
    return ",".join(fields)


def retry_on_exception(decorated):
    @functools.wraps(decorated)
    def wrapper(*args, **kwargs):
        retries = 0
        while True:
            try:
                return decorated(*args, **kwargs)
            except (IOError, requests.RequestException) as e:
                print(f"Tushare 发生I/O错误正在重试({retries}/10) {repr(e)}")
                retries += 1
                if retries > 10:
                    raise
                time.sleep(10)

    return wrapper

def retry_on_tushare_limit(max_retry: int = 1000, sleep_seconds: int = 60):
    """
    当tushare触发每分钟访问限制时自动等待并重试
    """

    def decorator(func):

        @functools.wraps(func)
        def wrapper(*args, **kwargs):

            for attempt in range(max_retry):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    err_msg = str(e)
                    if "每分钟最多访问" in err_msg or "rate limit" in err_msg:
                        print(
                            f"Tushare接口限速，等待 {sleep_seconds}s 后重试 "
                            f"({attempt+1}/{max_retry})"
                        )
                        time.sleep(sleep_seconds)
                        continue
                    raise
            raise RuntimeError("达到最大重试次数")
        return wrapper

    return decorator



class TushareClient:

    def __init__(
        self,
        token: str | None = None,
        env_path: str | Path | None = None,
    ) -> None:
        project_root = find_project_root()
        resolved_env_path = Path(env_path) if env_path else project_root / ".env"
        env_values = load_env_file(resolved_env_path)
        token = token or os.getenv("TUSHARE_TOKEN") or env_values.get("TUSHARE_TOKEN")
        if not token:
            raise ValueError(
                "未找到 TUSHARE_TOKEN。请在项目根目录 .env 中配置 "
                "`TUSHARE_TOKEN=your_token`，或初始化 TushareClient(token=...)。"
            )

        self.token = token
        self.env_path = resolved_env_path
        ts.set_token(token)
        self.ts = ts
        self.pro = ts.pro_api()

    @staticmethod
    def parse_date_to_str(date_obj: date | str) -> str:
        """转换date类型， ‘2026-03-02’ 至日期字符 ‘20260302’

        :param date | str date_obj:
        :return str:
        """
        if isinstance(date_obj, date):
            return date_obj.strftime("%Y%m%d")

        if isinstance(date_obj, str):
            if "-" in date_obj:
                return datetime.strptime(date_obj, "%Y-%m-%d").strftime("%Y%m%d")
            if len(date_obj) == 8 and date_obj.isdigit():
                return date_obj

        raise ValueError(f"Unsupported date value: {date_obj!r}")

    @retry_on_exception
    def get_tushare_pro_bar(
        self, 
        ts_code: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        asset: Literal["E", "I", "C", "FT", "FD", "O", "CB"] | None = "E",
        adj: Literal["qfq", "hfq", "none"] | None = None,
        freq: Literal["D", "W", "M"] = "D",
        ma: list[int] = None,
        factors: list[str] = None,
        adjfactor: bool = False,
        fields: list[str] = None,
    ) -> pd.DataFrame:
        """
        tushare pro bar通用行情接口
            https://tushare.pro/document/2?doc_id=109
        :param str ts_code: 
        :param date start_date: 
        :param date end_date: 
        :param Literal["E", "I", "C", "FT", "FD", "O", "CB"] asset: E股票 I沪深指数 C数字货币 FT期货 FD基金 O期权 CB可转债
        :param Literal["qfq", "hfq", "none"] adj: 
        :param Literal["D", "W", "M"] freq: 
        :param list[int] ma: 
        :param list[str] factors: 
        :param bool adjfactor: 
        :param list[str] fields: 
        :return pd.DataFrame:
        """
        return self.ts.pro_bar(
            ts_code=ts_code,
            start_date=self.parse_date_to_str(start_date),
            end_date=self.parse_date_to_str(end_date),
            asset=asset,
            adj=adj,
            freq=freq,
            ma=ma,
            factors=factors,
            adjfactor=adjfactor,
            fields=_fields(fields),
        )

    @retry_on_exception
    def get_stock_basic(
        self,
        fields: list[str] = None,
    ) -> pd.DataFrame:
        """
        获取股票基本信息
        :param list[str] fields: 字段列表
        :return pd.DataFrame:
        """
        if fields is None:
            fields = [
                "ts_code",
                "symbol",
                "name",
                "area",
                "industry",
                "fullname",
                "enname",
                "cnspell",
                "market",
                "exchange",
                "curr_type",
                "list_status",
                "list_date",
                "delist_date",
                "is_hs",
                "act_name",
                "act_ent_type"
            ]

        df = self.pro.stock_basic(
            fields=_fields(fields)
        )

        return df
    
    @retry_on_exception
    def get_stock_st(
        self, 
        start: date,
        end: date,
        fields: list[str] = None,
    ) -> pd.DataFrame:
        """
        ST股票列表
            来自tushare: https://tushare.pro/document/2?doc_id=397
            描述：获取ST股票列表，可根据交易日期获取历史上每天的ST列表
            权限：3000积分起
            提示：每天上午9:20更新，单次请求最大返回1000行数据，可循环提取,本接口数据从20160101开始,太早历史无法补齐
        """
        if fields is None:
            fields = [
                "ts_code",
                "name",
                "trade_date",
                "type",
                "type_name"
            ]
        
        _start = self.parse_date_to_str(start)
        _end = self.parse_date_to_str(end)
        df = self.pro.stock_st(
            start_date=_start, end_date=_end, fields=_fields(fields)
        )
        
        return df

    @retry_on_exception
    def get_stock_daily(
        self,
        ts_code: str | None = None,
        start: date | None = None,
        end: date | None = None,
        trade_date: date | str | None = None,
        adjust: Literal["qfq", "hfq", "none"] = "qfq",
        fields: list[str] = None,
    ) -> pd.DataFrame:
        """
        获取股票日线行情
        :param str ts_code: 股票代码
        :param date start: 开始日期
        :param date end: 结束日期
        :param Literal["qfq", "hfq", "none"] adjust: 复权方式
        :param list[str] fields: 字段列表
        :return pd.DataFrame:
        """
        if fields is None:
            fields = [
                "ts_code",
                "trade_date",
                "open",
                "high",
                "low",
                "close",
                "pre_close",
                "change",
                "pct_chg",
                "vol",
                "amount",
            ]

        if trade_date is not None:
            df = self.pro.daily(
                trade_date=self.parse_date_to_str(trade_date),
                fields=_fields(fields),
            )
        elif ts_code is not None and start is not None and end is not None:
            _start = self.parse_date_to_str(start)
            _end = self.parse_date_to_str(end)
            df = self.ts.pro_bar(
                ts_code=ts_code,
                start_date=_start,
                end_date=_end,
                fields=_fields(fields),
                adj=adjust,
            )
        elif start is not None and end is not None:
            _start = self.parse_date_to_str(start)
            _end = self.parse_date_to_str(end)
            df = self.pro.daily(
                start_date=_start,
                end_date=_end,
                fields=_fields(fields),
            )
        else:
            raise ValueError("get_stock_daily requires trade_date or start/end.")

        # 涨跌幅 % -> 涨跌幅 decimal
        if "pct_chg" in df.columns:
            df["pct_chg"] = df["pct_chg"] / 100

        return df

    @retry_on_exception
    def get_etf_basic(
        self,
        ts_code: str | None = None,
        index_code: str | None = None,
        list_date: date | None = None,
        list_status: str | None = None,
        exchange: str | None = None,
        mgr: str | None = None,
        fields: list[str] = None,
    ):
        """
        获取ETF基础信息
            https://tushare.pro/document/2?doc_id=385
            接口：etf_basic
            描述：获取国内ETF基础信息，包括了QDII。数据来源与沪深交易所公开披露信息。
            限量：单次请求最大放回5000条数据（当前ETF总数未超过2000）
            权限：用户积8000积分可调取，具体请参阅积分获取办法
        :param str ts_code: ETF代码
        :param str index_code: 跟踪指数代码
        :param date list_date: 上市日期（格式：YYYYMMDD）
        :param str list_status: 上市状态（L上市 D退市 P待上市）
        :param str exchange: 交易所（SH上交所 SZ深交所）
        :param str mgr: 管理人（简称，e.g.华夏基金)
        :param list[str] fields: 字段列表
        :return pd.DataFrame:
        """
        if fields is None:
            fields = [
                "ts_code",
                "csname",
                "extname",
                "cname",
                "index_code",
                "index_name",
                "setup_date",
                "list_date",
                "list_status",
                "exchange",
                "mgr_name",
                "custod_name",
                "mgt_fee",
                "etf_type"
            ]
        
        df = self.pro.etf_basic(
            ts_code=ts_code,
            index_code=index_code,
            list_date=self.parse_date_to_str(list_date) if list_date else None,
            list_status=list_status,
            exchange=exchange,
            mgr=mgr,
            fields=_fields(fields),
        )

        return df

    @retry_on_exception
    def get_fund_daily(
        self, 
        ts_code: str | None = None,
        trade_date: date | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        fields: list[str] = None,
    ) -> pd.DataFrame:
        """
        获取ETF日线行情
            https://tushare.pro/document/2?doc_id=109
            描述：获取ETF行情每日收盘后成交数据，历史超过10年
            限量：单次最大5000行记录，可以根据ETF代码和日期循环获取历史，总量不限制
            权限：需要至少5000积分才可以调取，8000积分频次更高
        :param str ts_code: 基金代码
        :param date trade_date: 交易日期
        :param date start_date: 开始日期
        :param date end_date: 结束日期
        :param list[str] fields: 字段列表
        :return pd.DataFrame:
        """
        if fields is None:
            fields = [
                "ts_code",
                "trade_date",
                "open",
                "high",
                "low",
                "close",
                "pre_close",
                "change",
                "pct_chg",
                "vol",
                "amount",
            ]

        df = self.pro.fund_daily(
            ts_code=ts_code,
            trade_date=self.parse_date_to_str(trade_date) if trade_date else None,
            start_date=self.parse_date_to_str(start_date) if start_date else None,
            end_date=self.parse_date_to_str(end_date) if end_date else None,
            fields=_fields(fields),
        )

        # 涨跌幅 % -> 涨跌幅 decimal
        if "pct_chg" in df.columns:
            df["pct_chg"] = df["pct_chg"] / 100
        
        return df

    @retry_on_exception
    def get_index_basic(
        self, 
        ts_code: str | None = None,
        symbol: str | None = None,
        name: str | None = None,
        market: str | None = None,
        publisher: str | None = None,
        category: str | None = None,
        fields: list[str] = None,
    ) -> pd.DataFrame:
        """
        获取指数基础信息
            https://tushare.pro/document/2?doc_id=94
        :param str ts_code: ts指数代码
        :param str symbol: 指数代码，支持多值输入，如000300,000001
        :param str name: 指数简称
        :param str market: 交易所或服务商(默认SSE); MSCI(MSCI指数), CSI(中证指数), SSE(上交所指数), SZSE(深交所指数), CICC(中金指数), SW(申万指数), OTH(其他指数)
        :param str publisher: 发布商
        :param str category: 指数类别
        :param list[str] fields: 字段列表
        :return pd.DataFrame:
        """
        if fields is None:
            fields = [
                "ts_code",
                "name",
                "fullname",
                "market",
                "publisher",
                "index_type",
                "category",
                "base_date",
                "base_point",
                "list_date",
                "weight_rule",
                "exp_date",
            ]
        
        return self.pro.index_basic(
            ts_code=ts_code,
            symbol=symbol,
            name=name,
            market=market,
            publisher=publisher,
            category=category,
            fields=_fields(fields),
        )
    
    @retry_on_exception
    def get_index_daily(
        self, 
        ts_code: str | None = None,
        trade_date: date | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        fields: list[str] | None = None,
    ) -> pd.DataFrame:
        """
        获取指数日线行情
            https://tushare.pro/document/2?doc_id=95
            描述：获取指数每日行情，还可以通过bar接口获取。目前规则是单次调取最多取8000行记录，可以设置start和end日期补全。指数行情也可以通过通用行情接口获取数据。
        :param ts_code: 指数代码
        :param trade_date: 交易日期
        :param start_date: 开始日期
        :param end_date: 结束日期
        :param fields: 指定字段
        :return: 指数日线行情
        """

        if fields is None:
            fields = [
                "ts_code",
                "trade_date",
                "open",
                "high",
                "low",
                "close",
                "pre_close",
                "change",
                "pct_chg",
                "vol",
                "amount",
            ]

        df = self.pro.index_daily(
            ts_code=ts_code,
            trade_date=self.parse_date_to_str(trade_date) if trade_date else None,
            start_date=self.parse_date_to_str(start_date) if start_date else None,
            end_date=self.parse_date_to_str(end_date) if end_date else None,
            fields=_fields(fields),
        )

        # 涨跌幅 % -> 涨跌幅 decimal
        if "pct_chg" in df.columns:
            df["pct_chg"] = df["pct_chg"] / 100
        
        return df
    
    @retry_on_exception
    def get_future_basic(
        self, 
        exchange: Literal["CFFEX", "DCE", "CZCE", "SHFE", "INE", "GFEX"] | None = None, 
        fut_type: str | None = None, 
        fut_code: str | None = None,
        list_date: date | None = None,
        fields: list[str] | None = None
    ):
        """
        获取期货合约信息表
            https://tushare.pro/document/2?doc_id=135
        param exchange: 交易所代码
        param fut_type: 合约类型(1 普通合约 2主力与连续合约 默认取全部)
        param fut_code: 合约代码, 标准合约代码，如白银AG、AP鲜苹果等
        param list_date: 上市日期，上市开始日期(格式YYYYMMDD，从某日开始以来所有合约）
        param fields: 指定字段
        """
        if fields is None:
            fields = [
                "ts_code",
                "symbol",
                "exchange",
                "name",
                "fut_code",
                "multiplier",
                "trade_unit",
                "per_unit",
                "quote_unit",
                "quote_unit_desc",
                "d_mode_desc",
                "list_date",
                "delist_date",
                "d_month",
                "last_ddate",
            ]
        
        return self.pro.fut_basic(
            exchange=exchange,
            fut_type=fut_type,
            fut_code=fut_code,
            list_date=self.parse_date_to_str(list_date) if list_date else None,
            fields=fields,
        )

    @retry_on_exception
    def get_future_daily(
        self,
        ts_code: str | None = None,
        trade_date:date | None = None,
        exchange: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        fields: list[str] | None = None,
    ) -> pd.DataFrame:

        if fields is None:
            fields = [
                "ts_code",
                "trade_date",
                "pre_close",
                "pre_settle",
                "open",
                "high",
                "low",
                "close",
                "settle",
                "change1",
                "change2",
                "vol",
                "amount",
                "oi",
                "oi_chg",
                "delv_settle",
            ]
        
        return self.pro.fut_daily(
            ts_code=ts_code,
            trade_date=self.parse_date_to_str(trade_date) if trade_date else None,
            exchange=exchange,
            start_date=self.parse_date_to_str(start_date) if start_date else None,
            end_date=self.parse_date_to_str(end_date) if end_date else None,
            fields=fields,
        )







if __name__ == "__main__":
    client = TushareClient()
    # df = client.get_stock_basic()
    # print(df)
    # df = client.get_stock_daily(
    #     ts_code="000001.SZ",
    #     start=date(2026, 5, 1),
    #     end=date(2026, 5, 27),
    #     adjust="qfq",
    # )
    # print(df)

    # df = client.get_etf_basic()
    # print(df)
    # df = client.get_fund_daily(
    #     ts_code="510300.SH",
    #     start_date=date(2026, 5, 27),
    #     end_date=date(2026, 5, 27),
    # )
    # print(df)

    # df = client.get_index_basic(market="SZSE")
    # print(df)
    # df = client.get_index_daily(
    #     ts_code="000001.SH",
    #     start_date=date(2026, 5, 27),
    #     end_date=date(2026, 5, 27),
    # )
    # print(df)

    df = client.get_future_basic()
