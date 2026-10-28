"""
美股交易时段判断（美东时间，不含节假日）

| 时段 | 时间 (ET)          |
|------|-------------------|
| 盘前 | 4:00 - 9:30       |
| 盘中 | 9:30 - 16:00      |
| 盘后 | 16:00 - 20:00     |
| 夜盘 | 20:00 - 次日 4:00 |
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def _weekday_minutes_between(start: int, end: int) -> bool:
    now = datetime.now(ET)
    if now.weekday() >= 5:
        return False
    minutes = now.hour * 60 + now.minute
    return start <= minutes < end


def is_regular_hours() -> bool:
    """盘中 9:30-16:00 ET"""
    return _weekday_minutes_between(9 * 60 + 30, 16 * 60)


def is_premarket_hours() -> bool:
    """盘前 4:00-9:30 ET"""
    return _weekday_minutes_between(4 * 60, 9 * 60 + 30)


def is_tradable_hours() -> bool:
    """可自动交易时段：盘前 + 盘中 + 盘后 4:00-20:00 ET，夜盘不交易"""
    return _weekday_minutes_between(4 * 60, 20 * 60)


def is_watch_hours() -> bool:
    """监听时段：盘前 + 盘中 4:00-16:00 ET，盘后、夜盘、周末不轮询"""
    return _weekday_minutes_between(4 * 60, 16 * 60)


def next_watch_start() -> datetime:
    """下一个监听时段的开始时间（工作日 4:00 ET）"""
    now = datetime.now(ET)
    start = now.replace(hour=4, minute=0, second=0, microsecond=0)
    if now >= start:
        start += timedelta(days=1)
    while start.weekday() >= 5:
        start += timedelta(days=1)
    return start
