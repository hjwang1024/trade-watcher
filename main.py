"""
富途组合监听 / 跟单主程序

实时监听组合持仓变化并推送 Bark 通知；config.COPYTRADE_ENABLED = True 时同时自动跟单。
只在美股盘前 + 盘中（4:00-16:00 ET）轮询，启动时无论什么时段都会先检查一次。
只有持仓和上次抓到的相比有变动才推送；--once 会额外推送一次当前持仓，用来测试推送通道。

用法:
    python main.py          # 持续监听（按配置决定是否跟单）
    python main.py --once   # 只检查一次并推送当前持仓（测试推送用）
    python main.py --dry    # 跟单干跑模式，只检测变化不下单
"""

import logging
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

import config
from market_hours import is_watch_hours, next_watch_start
from monitor import Watcher, fetch_portfolio
from notify import notify_error, notify_monitor_down, notify_monitor_recovered

logger = logging.getLogger(__name__)

# 请求失败时的最长退避间隔（秒）
MAX_BACKOFF = 300
# 非监听时段单次最长休眠（秒），分段睡以免电脑休眠唤醒后错过开盘
MAX_IDLE_SLEEP = 600


def setup_logging():
    log_file = Path(__file__).parent / config.LOG_FILE
    log_file.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(),
            RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"),
        ],
    )


def next_interval(failures: int) -> float:
    """连续失败时指数退避，避免被限流后越请求越被封"""
    if failures == 0:
        return config.POLL_INTERVAL
    return min(config.POLL_INTERVAL * 2 ** min(failures, 6), MAX_BACKOFF)


def wait_for_watch_hours():
    """盘后、夜盘、周末暂停轮询，阻塞到下一个盘前开盘"""
    if is_watch_hours():
        return
    logger.info("非监听时段，暂停到 %s ET", next_watch_start().strftime("%Y-%m-%d %H:%M"))
    while not is_watch_hours():
        remaining = next_watch_start().timestamp() - time.time()
        time.sleep(min(max(remaining, 1), MAX_IDLE_SLEEP))
    logger.info("进入监听时段，恢复轮询")


def main():
    dry_run = "--dry" in sys.argv
    once = "--once" in sys.argv
    setup_logging()

    portfolio_id = config.PORTFOLIO_ID
    watcher = Watcher(portfolio_id, announce=once)
    portfolio_name = watcher.portfolio_name
    logger.info("=== 启动 ===")
    logger.info("组合: %s (%s)", portfolio_name, portfolio_id)
    logger.info("轮询间隔: %ss | 监听时段: 盘前 + 盘中 4:00-16:00 ET", config.POLL_INTERVAL)
    if config.COPYTRADE_ENABLED:
        logger.info("跟单: 开启%s | 交易环境: %s | 跟单资金: $%s",
                    " (干跑)" if dry_run else "", config.TRADE_ENV, f"{config.TOTAL_CAPITAL:,.0f}")
    else:
        logger.info("跟单: 关闭（只监听通知，不下单）")

    copytrader = None
    if config.COPYTRADE_ENABLED:
        # 只有开启跟单才需要 moomoo SDK 和 OpenD
        from copytrade import CopyTrader
        copytrader = CopyTrader(portfolio_id, portfolio_name, dry_run=dry_run)
        copytrader.initialize()

    failures = 0
    while True:
        try:
            positions = fetch_portfolio(portfolio_id)
            if positions is None:
                failures += 1
                if failures == config.FAILURE_ALERT_THRESHOLD:
                    notify_monitor_down(portfolio_name, failures)
            else:
                if failures >= config.FAILURE_ALERT_THRESHOLD:
                    notify_monitor_recovered(portfolio_name)
                failures = 0
                watcher.check(positions)
                if copytrader:
                    copytrader.on_tick(positions)
        except KeyboardInterrupt:
            logger.info("用户中断，退出")
            break
        except Exception as e:
            logger.error("运行出错: %s", e, exc_info=True)
            notify_error(str(e))

        if once:
            break
        try:
            time.sleep(next_interval(failures))
            wait_for_watch_hours()
        except KeyboardInterrupt:
            logger.info("用户中断，退出")
            break


if __name__ == "__main__":
    main()
