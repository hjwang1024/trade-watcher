"""
跟单模块：根据组合持仓变化在自己账户自动下单

仅在 config.COPYTRADE_ENABLED = True 时由 main.py 加载。
- 启动时交互式初始化跟单持仓，区分跟单仓位和底仓
- 可交易时段（4:00-20:00 ET）检测到变化立即执行
- 夜盘检测到变化先记录账户持仓，盘前对账后再执行
"""

import logging

import config
import storage
from market_hours import is_premarket_hours, is_regular_hours, is_tradable_hours
from monitor import diff_positions, fetch_portfolio, has_changes, load_snapshot, save_snapshot
from notify import notify_deferred
from trader import COPYTRADE_POS_FILE, Trader

logger = logging.getLogger(__name__)

# 跟单基准快照：上次已执行（或已确认无需执行）的组合持仓
SNAPSHOT = "trade_snapshot.json"
# 夜盘检测到变化时的账户持仓，用于盘前对账
OVERNIGHT_ACCT = "overnight_account_snapshot.json"
# 夜盘启动时用户选择买入、等待盘前执行的股票 [{code, weight}]
PENDING_BUYS = "pending_buys.json"


def _ask_int(prompt, max_value):
    while True:
        answer = input(prompt).strip()
        if answer == "":
            return 0
        if answer.lower() == "all":
            return max_value
        try:
            value = int(answer)
            if 0 <= value <= max_value:
                return value
            print(f"    请输入 0 到 {max_value} 之间的整数")
        except ValueError:
            print("    请输入整数、all 或直接回车")


def _ask_yes(prompt):
    while True:
        answer = input(prompt).strip().lower()
        if answer in ("y", "yes", "是"):
            return True
        if answer in ("n", "no", "否", ""):
            return False
        print("    请输入 y 或 n")


class CopyTrader:
    def __init__(self, portfolio_id, portfolio_name, dry_run=False):
        self.portfolio_id = portfolio_id
        self.portfolio_name = portfolio_name
        self.dry_run = dry_run
        self._snapshot = load_snapshot(SNAPSHOT, portfolio_id)

    def initialize(self):
        """每次启动时重新初始化跟单持仓记录。

        对于组合中每只股票:
        - 账户有持仓 → 询问其中多少股算跟单（其余为底仓，不会被自动卖出）
        - 账户无持仓 → 询问是否买入（可交易时段立即买，夜盘等盘前买）
        """
        print("\n" + "=" * 50)
        print("跟单初始化" + (" (干跑模式，不会下单)" if self.dry_run else ""))
        print("=" * 50)

        portfolio = fetch_portfolio(self.portfolio_id)
        if portfolio is None:
            raise RuntimeError("无法获取组合持仓，初始化失败")

        print(f"\n当前组合持仓 ({len(portfolio)} 只):")
        for p in portfolio:
            print(f"  {p['code']} {p['name']} 权重={p['weight']:.1%}")

        with Trader(unlock=False) as trader:
            acct_positions = trader.get_my_positions()
        print(f"\n当前账户持仓 ({len(acct_positions)} 只):")
        for code, qty in sorted(acct_positions.items()):
            print(f"  {code}: {qty} 股")

        print("\n" + "-" * 50)
        print("请为组合中的每只股票设置跟单状态:")
        print("-" * 50)

        ct_pos = {}
        buys = []
        for p in portfolio:
            code, name, weight = p["code"], p["name"], p["weight"]
            acct_qty = acct_positions.get(code, 0)
            if acct_qty > 0:
                ct_qty = _ask_int(f"\n  {code} {name} (持有 {acct_qty} 股)\n  跟单数量 (回车=0, all=全部): ", acct_qty)
                if ct_qty > 0:
                    ct_pos[code] = ct_qty
            elif _ask_yes(f"\n  {code} {name} (未持有, 权重={weight:.1%})\n  是否买入? (y/n): "):
                buys.append({"code": code, "weight": weight})
            else:
                print(f"    跳过 {code}")

        storage.save_json(COPYTRADE_POS_FILE, ct_pos)
        storage.remove(OVERNIGHT_ACCT)
        self._update_snapshot(portfolio)
        print(f"\n跟单持仓记录已保存: {ct_pos or '空'}")

        if buys:
            storage.save_json(PENDING_BUYS, buys)
            if is_tradable_hours():
                print(f"\n执行买入 ({len(buys)} 只)...")
                self._run_pending_buys()
            else:
                print(f"\n夜盘不可交易，{len(buys)} 只待买入将在盘前自动执行")
        else:
            storage.remove(PENDING_BUYS)

        print("\n" + "=" * 50)
        print("初始化完成")
        print("=" * 50 + "\n")

    def on_tick(self, positions):
        """每次轮询拿到组合持仓后调用"""
        tradable = is_tradable_hours()
        if tradable:
            self._run_pending_buys()

        if self._snapshot is None:
            self._update_snapshot(positions)
            return

        diff = diff_positions(self._snapshot, positions)
        if not has_changes(diff):
            # 无变化：吸收股价波动带来的权重漂移；可交易时段没有待执行变化，夜盘对账快照已无用
            self._update_snapshot(positions)
            if tradable:
                storage.remove(OVERNIGHT_ACCT)
            return

        if not tradable:
            self._on_overnight_change()
            return  # 保留旧快照，等可交易时段再执行

        self._execute(diff, positions)
        self._update_snapshot(positions)

    def _update_snapshot(self, positions):
        self._snapshot = positions
        save_snapshot(SNAPSHOT, self.portfolio_id, positions)

    def _on_overnight_change(self):
        """夜盘首次检测到变化：记录账户持仓，盘前用来识别期间的手动操作"""
        if storage.exists(OVERNIGHT_ACCT):
            return
        with Trader(unlock=False) as trader:
            acct = trader.get_my_positions()
        storage.save_json(OVERNIGHT_ACCT, acct)
        logger.info("夜盘检测到变化，账户持仓快照已保存: %s，将在盘前自动执行", acct or "空")
        notify_deferred(self.portfolio_name)

    def _skip_premarket_sells(self, old_count, new_count):
        if not is_premarket_hours():
            return False
        mode = config.PREMARKET_SELL_MODE
        if mode == "never":
            return True
        if mode == "same_count" and old_count == 5 and new_count == 5:
            logger.info("盘前换仓模式: 5只→5只, 跳过卖出")
            return True
        return False

    def _execute(self, diff, positions):
        regular = is_regular_hours()
        if self.dry_run:
            logger.info("干跑模式，不执行交易 (%s)", "市价单" if regular else "限价单")
            return

        skip_sells = self._skip_premarket_sells(len(self._snapshot), len(positions))
        overnight_acct = storage.load_json(OVERNIGHT_ACCT)
        with Trader() as trader:
            if overnight_acct is not None:
                trader.reconcile_overnight(overnight_acct, {p["code"] for p in positions})
            trader.execute_diff(diff, use_market_order=regular, skip_sells=skip_sells)
        storage.remove(OVERNIGHT_ACCT)

    def _run_pending_buys(self):
        buys = storage.load_json(PENDING_BUYS, [])
        if not buys:
            return
        if self.dry_run:
            logger.info("干跑模式，跳过待买入: %s", ", ".join(b["code"] for b in buys))
            storage.remove(PENDING_BUYS)
            return

        logger.info("处理待买入列表 (%d 只)...", len(buys))
        with Trader() as trader:
            held = trader.get_my_positions()
            tasks = []
            for b in buys:
                code = b["code"]
                current_qty = held.get(code, 0)
                buy_qty = trader.calc_target_qty(code, b["weight"]) - current_qty
                if buy_qty > 0:
                    tasks.append((code, buy_qty))
                elif current_qty > 0:
                    logger.info("待买入 %s 用户已手动买入 %d 股, 更新跟单记录", code, current_qty)
                    trader.update_ct_pos(code, current_qty)
                else:
                    logger.info("待买入 %s 目标数量为0, 跳过", code)
            # 先清掉列表再下单，避免下单过程中出错导致下次重复买入
            storage.remove(PENDING_BUYS)
            if tasks:
                trader.execute_buys(tasks, use_market_order=is_regular_hours())
