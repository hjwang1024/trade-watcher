"""
查看持仓（只读，不下单）

用法:
    python positions.py            # 查看跟踪组合的持仓
    python positions.py --account  # 同时查看自己账户持仓（需要 OpenD 运行）
"""

import sys

import config
from monitor import fetch_portfolio, fetch_summary
from notify import format_report


def show_portfolio():
    summary = fetch_summary(config.PORTFOLIO_ID)
    positions = fetch_portfolio(config.PORTFOLIO_ID)
    name = summary["name"] if summary else config.PORTFOLIO_ID
    print(f"=== {name} ({config.PORTFOLIO_ID}) ===")
    if positions is None:
        print("获取持仓失败，请查看上方错误信息")
        return
    print(format_report(summary, positions))


def show_account():
    from trader import Trader

    print(f"\n=== 账户持仓 ({config.TRADE_ENV}) ===")
    with Trader(unlock=False) as trader:
        data = trader.query_positions()
    if data.empty:
        print("  无持仓")
        return
    cols = ["code", "stock_name", "qty", "cost_price", "market_val", "pl_ratio"]
    print(data[[c for c in cols if c in data.columns]].to_string(index=False))


if __name__ == "__main__":
    show_portfolio()
    if "--account" in sys.argv:
        show_account()
