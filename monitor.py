"""
监控模块：抓取富途公开组合持仓、对比变化、推送通知（只读，不涉及交易）
"""

import logging
import time
from datetime import datetime

import requests

import config
import storage
from market_hours import ET
from notify import format_diff, notify_changes, notify_startup

logger = logging.getLogger(__name__)

API_BASE = "https://portfolio.futunn.com/portfolio-api"

# 接口里的比例和价格都以 10 亿为基数
SCALE = 1_000_000_000

MARKET_MAP = {1: "HK", 2: "US", 3: "SH", 4: "SZ"}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Referer": "https://www.futunn.com/",
    "Origin": "https://www.futunn.com",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

_session = requests.Session()
_session.headers.update(HEADERS)


def _get(endpoint: str, portfolio_id: str) -> dict:
    params = {"portfolio_id": portfolio_id, "language": 0, "_": int(time.time() * 1000)}
    headers = {"Cookie": config.FUTU_COOKIE} if config.FUTU_COOKIE else None
    resp = _session.get(f"{API_BASE}/{endpoint}", params=params, headers=headers, timeout=15)
    resp.raise_for_status()
    result = resp.json()
    if result.get("code") != 0:
        raise RuntimeError(f"API 返回错误: {result.get('message') or result.get('code')}")
    return result.get("data") or {}


def fetch_portfolio_info(portfolio_id: str) -> dict | None:
    """组合名称和净值（nav = 1 + 总收益），失败返回 None"""
    try:
        data = _get("get-portfolio-info", portfolio_id)
    except Exception as e:
        logger.warning("获取组合信息失败: %s", e)
        return None
    return {"name": data.get("name") or str(portfolio_id), "nav": data.get("net_value", 0) / SCALE}


class MonthlyReturn:
    """本月收益 = 当前净值 / 上月最后记录的净值 - 1

    富途接口只给总收益，月初净值需要自己记录：跨月时把上个月最后一次看到的净值作为基准。
    首次运行的当月、或上个月程序没运行过，没有基准，返回 None。
    """

    FILE = "nav.json"

    def __init__(self, portfolio_id: str):
        self.portfolio_id = str(portfolio_id)
        state = storage.load_json(self.FILE) or {}
        self._state = state if state.get("portfolio_id") == self.portfolio_id else {"portfolio_id": self.portfolio_id}

    @staticmethod
    def _prev_month(month: str) -> str:
        year, mon = map(int, month.split("-"))
        return f"{year - 1}-12" if mon == 1 else f"{year}-{mon - 1:02d}"

    def update(self, nav: float) -> tuple[int, float | None]:
        """记录当前净值，返回 (当前月份, 本月收益)"""
        month = datetime.now(ET).strftime("%Y-%m")
        state = self._state
        if state.get("month") != month:
            last_month_ok = state.get("month") == self._prev_month(month)
            state["base_nav"] = state.get("last_nav") if last_month_ok else None
            state["month"] = month
        state["last_nav"] = nav
        storage.save_json(self.FILE, state)
        base = state.get("base_nav")
        return int(month[-2:]), (nav / base - 1 if base else None)


def fetch_summary(portfolio_id: str) -> dict | None:
    """组合名称 + 收益概况，失败返回 None"""
    info = fetch_portfolio_info(portfolio_id)
    if info is None:
        return None
    month, month_return = MonthlyReturn(portfolio_id).update(info["nav"])
    return {
        "name": info["name"],
        "month": month,
        "month_return": month_return,
        "total_return": info["nav"] - 1,
    }


def fetch_portfolio(portfolio_id: str) -> list[dict] | None:
    """
    抓取公开组合持仓，失败返回 None。

    返回格式:
    [
        {
            "code": "US.NBIS",
            "name": "NEBIUS",
            "weight": 0.20,       # 持仓权重 (0-1)，含挂单中的部分
            "is_pending": False,  # 是否有挂单未成交
            "cost_price": 168.19,
            "pl_ratio": 0.0194,   # 盈亏比例
        },
        ...
    ]
    """
    try:
        data = _get("get-portfolio-position", portfolio_id)
    except Exception as e:
        logger.error("请求组合数据失败: %s", e)
        return None

    positions = []
    for item in data.get("record_items") or []:
        positions.append({
            "code": f"{MARKET_MAP.get(item['market'], 'US')}.{item['stock_code']}",
            "name": item.get("stock_name", ""),
            # 用 total_ratio（已成交 + 待成交），这样非盘中的挂单也能捕捉到
            "weight": round(item.get("total_ratio", 0) / SCALE, 6),
            "is_pending": item.get("pending_ratio", 0) > 0,
            "cost_price": round(item.get("cost_price", 0) / SCALE, 4),
            "pl_ratio": round(item.get("profit_and_loss_ratio", 0) / SCALE, 6),
        })

    logger.debug("获取到 %d 只持仓: %s", len(positions),
                 ", ".join(f"{p['code']}({p['weight']:.1%})" for p in positions))
    return positions


def load_snapshot(name: str, portfolio_id: str) -> list[dict] | None:
    """加载持仓快照。不存在或属于其他组合时返回 None"""
    snap = storage.load_json(name)
    if not isinstance(snap, dict) or snap.get("portfolio_id") != str(portfolio_id):
        return None
    return snap["positions"]


def save_snapshot(name: str, portfolio_id: str, positions: list[dict]):
    storage.save_json(name, {"portfolio_id": str(portfolio_id), "positions": positions})


def diff_positions(old: list[dict], new: list[dict]) -> dict:
    """
    对比新旧持仓，返回变化:
    {
        "added":   [...],  # 新买入
        "removed": [...],  # 已清仓
        "changed": [...],  # 调仓（权重变化超过阈值）
        "filled":  [...],  # 挂单成交（权重不变，仅用于通知，跟单不处理）
    }
    """
    old_map = {p["code"]: p for p in old}
    new_map = {p["code"]: p for p in new}

    added = [p for code, p in new_map.items() if code not in old_map]
    removed = [p for code, p in old_map.items() if code not in new_map]

    changed = []
    filled = []
    for code, new_pos in new_map.items():
        old_pos = old_map.get(code)
        if old_pos is None:
            continue
        old_w = old_pos.get("weight", 0)
        new_w = new_pos.get("weight", 0)
        if abs(new_w - old_w) > config.WEIGHT_CHANGE_THRESHOLD:
            changed.append({**new_pos, "old_weight": old_w, "new_weight": new_w})
        elif old_pos.get("is_pending") and not new_pos.get("is_pending"):
            filled.append(new_pos)

    return {"added": added, "removed": removed, "changed": changed, "filled": filled}


def has_changes(diff: dict) -> bool:
    """是否有需要跟单的持仓变化（不含挂单成交）"""
    return bool(diff["added"] or diff["removed"] or diff["changed"])


class Watcher:
    """监听组合持仓变化并推送通知

    每次都和上一次抓取的结果比较，股价波动导致的权重缓慢漂移不会累积成误报。
    快照落盘，程序重启后能发现停机期间的调仓。
    """

    SNAPSHOT = "watch_snapshot.json"
    # 收益概况刷新间隔（秒），有变化时立即刷新
    SUMMARY_REFRESH = 600

    def __init__(self, portfolio_id: str):
        self.portfolio_id = portfolio_id
        self.portfolio_name = str(portfolio_id)
        self._summary = None
        self._summary_time = 0.0
        self._announced = False
        self._last = load_snapshot(self.SNAPSHOT, portfolio_id)
        self._refresh_summary(force=True)

    def _refresh_summary(self, force=False):
        if not force and time.time() - self._summary_time < self.SUMMARY_REFRESH:
            return
        self._summary_time = time.time()
        summary = fetch_summary(self.portfolio_id)
        if summary:
            self._summary = summary
            self.portfolio_name = summary["name"]

    def check(self, positions: list[dict]) -> dict | None:
        """对比上次持仓，有变化则推送通知并返回 diff，无变化返回 None。首次调用推送开始监听"""
        last, self._last = self._last, positions
        save_snapshot(self.SNAPSHOT, self.portfolio_id, positions)
        diff = diff_positions(last, positions) if last is not None else None
        changed = diff is not None and (has_changes(diff) or bool(diff["filled"]))
        self._refresh_summary(force=changed)

        if not self._announced:
            self._announced = True
            notify_startup(self.portfolio_name, self._summary, positions)
        if not changed:
            return None

        logger.info("检测到持仓变化:")
        for line in format_diff(diff):
            logger.info("  %s", line)
        notify_changes(self.portfolio_name, self._summary, diff, positions)
        return diff
