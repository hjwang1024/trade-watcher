"""
交易模块：通过 moomoo-api 连接 OpenD 下单、追单、查询持仓

限价单下单后轮询检查，未成交则改价重挂，超过最大次数撤单（部分成交照常记录）。
价格数据通过 yfinance 获取。
跟单持仓独立记录在 data/copytrade_positions.json，卖出时只卖跟单部分，不动底仓。

用法:
    with Trader() as trader:              # 连接并解锁交易
        trader.execute_diff(diff, ...)
    with Trader(unlock=False) as trader:  # 只读查询，不解锁
        trader.get_my_positions()
"""

import logging
import time

import yfinance as yf
from moomoo import (
    OpenSecTradeContext,
    TrdEnv,
    TrdMarket,
    TrdSide,
    OrderType,
    OrderStatus,
    ModifyOrderOp,
    RET_OK,
    SecurityFirm,
)

import config
import storage
from market_hours import is_regular_hours
from notify import notify_order_filled, notify_order_timeout, notify_error
from stoploss import place_stop_loss

logger = logging.getLogger(__name__)

TRD_ENV_MAP = {
    "SIMULATE": TrdEnv.SIMULATE,
    "REAL": TrdEnv.REAL,
}

TRD_MARKET_MAP = {
    "US": TrdMarket.US,
    "HK": TrdMarket.HK,
}

# 已终结的订单状态
TERMINAL_STATUSES = {
    OrderStatus.FILLED_ALL,
    OrderStatus.CANCELLED_ALL,
    OrderStatus.CANCELLED_PART,
    OrderStatus.FAILED,
    OrderStatus.SUBMIT_FAILED,
    OrderStatus.DISABLED,
    OrderStatus.DELETED,
    OrderStatus.FILL_CANCELLED,
}

STOP_ORDER_TYPES = {"STOP_LIMIT", "TRAILING_STOP_LIMIT", "TRAILING_STOP"}

# 跟单持仓记录文件 {code: qty}
COPYTRADE_POS_FILE = "copytrade_positions.json"


def _side_name(side):
    return "买入" if side == TrdSide.BUY else "卖出"


def _num(value) -> float:
    """DataFrame 里的数值可能是 None / NaN / 'N/A'，统一转成 float，无效值返回 0"""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return 0.0
    return value if value == value else 0.0


class Trader:
    def __init__(self, unlock=True):
        self.trd_env = TRD_ENV_MAP[config.TRADE_ENV]
        self.unlock = unlock
        self._trd_ctx = None
        self._stop_orders = {}  # code -> stop_loss_order_id
        self._ct_pos = storage.load_json(COPYTRADE_POS_FILE, {})  # 跟单持仓

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *exc):
        self.close()

    def connect(self):
        self._trd_ctx = OpenSecTradeContext(
            security_firm=SecurityFirm.FUTUINC,
            host=config.OPEND_HOST, port=config.OPEND_PORT,
            filter_trdmarket=TRD_MARKET_MAP[config.TRADE_MARKET],
        )
        logger.info("已连接 OpenD %s:%s (%s)", config.OPEND_HOST, config.OPEND_PORT, config.TRADE_ENV)
        if not self.unlock:
            return
        try:
            ret, data = self._trd_ctx.unlock_trade(config.TRADE_PASSWORD)
            if ret != RET_OK:
                raise RuntimeError("解锁交易失败: %s" % data)
            logger.info("交易已解锁")
            self.sync_stop_orders()
        except Exception:
            self.close()
            raise
        logger.info("跟单持仓: %s", self._ct_pos or "空")

    def close(self):
        if self._trd_ctx:
            self._trd_ctx.close()
            self._trd_ctx = None
            logger.info("已断开 OpenD 连接")

    # ========== 跟单持仓记录 ==========

    def update_ct_pos(self, code, delta):
        """更新跟单持仓记录，delta>0 买入，delta<0 卖出"""
        current = self._ct_pos.get(code, 0)
        new_qty = max(0, current + delta)
        if new_qty > 0:
            self._ct_pos[code] = new_qty
        else:
            self._ct_pos.pop(code, None)
        storage.save_json(COPYTRADE_POS_FILE, self._ct_pos)
        logger.info("跟单持仓更新 %s: %d -> %d", code, current, new_qty)

    def reconcile_overnight(self, overnight_acct: dict, portfolio_codes: set):
        """盘前对账：比较夜盘快照和当前实际持仓，补录用户手动操作。

        - 当前持仓比夜盘快照多的部分 = 用户手动买入，补录到跟单记录
        - 当前持仓比夜盘快照少的部分 = 用户手动卖出，从跟单记录扣除
        只对账组合中的股票，避免把底仓误录为跟单。
        """
        current_acct = self.get_my_positions()
        logger.info("盘前对账: 夜盘快照=%s, 当前持仓=%s", overnight_acct, current_acct)

        for code in (set(overnight_acct) | set(current_acct)) & portfolio_codes:
            old_qty = overnight_acct.get(code, 0)
            new_qty = current_acct.get(code, 0)
            delta = new_qty - old_qty
            if delta > 0:
                logger.info("对账: %s 持仓增加 %d 股 (%d -> %d), 补录到跟单记录",
                            code, delta, old_qty, new_qty)
                self.update_ct_pos(code, delta)
            elif delta < 0:
                reduce = min(-delta, self._ct_pos.get(code, 0))
                if reduce > 0:
                    logger.info("对账: %s 持仓减少 %d 股 (%d -> %d), 从跟单记录扣除 %d",
                                code, -delta, old_qty, new_qty, reduce)
                    self.update_ct_pos(code, -reduce)
                else:
                    logger.info("对账: %s 持仓减少 %d 股, 但跟单记录为0, 可能是底仓变动", code, -delta)

        logger.info("对账完成, 跟单持仓: %s", self._ct_pos or "空")

    # ========== 查询 ==========

    def query_positions(self):
        """查询账户持仓明细 DataFrame，失败抛异常"""
        ret, data = self._trd_ctx.position_list_query(trd_env=self.trd_env)
        if ret != RET_OK:
            raise RuntimeError("查询持仓失败: %s" % data)
        return data

    def get_my_positions(self) -> dict:
        """账户持仓 {code: qty}，失败抛异常（不能当作空仓，否则会误清跟单记录）"""
        data = self.query_positions()
        return {row["code"]: int(row["qty"]) for _, row in data.iterrows() if row["qty"] > 0}

    def get_price(self, code):
        """通过 yfinance 获取实时价格

        盘中用 regularMarketPrice，盘前/盘后优先用 preMarketPrice/postMarketPrice，
        都拿不到时用 fast_info.lastPrice 兜底。
        """
        symbol = code.split(".")[-1]
        try:
            ticker = yf.Ticker(symbol)
            try:
                info = ticker.info
                pre = info.get("preMarketPrice")
                post = info.get("postMarketPrice")
                regular = info.get("regularMarketPrice") or info.get("currentPrice")

                if is_regular_hours():
                    price, src = regular, "盘中"
                elif pre and pre > 0:
                    price, src = pre, "盘前"
                elif post and post > 0:
                    price, src = post, "盘后"
                else:
                    price, src = regular, "兜底(regularMarket)"

                if price and price > 0:
                    logger.info("yfinance %s价格 %s: %.2f", src, code, price)
                    return round(price, 2)
            except Exception as e:
                logger.warning("yfinance info 获取失败 %s: %s, 尝试 fast_info", code, e)

            fast = ticker.fast_info
            price = getattr(fast, "lastPrice", None) or getattr(fast, "last_price", None)
            if price and price > 0:
                logger.info("yfinance fast_info 价格 %s: %.2f (可能为收盘价)", code, price)
                return round(price, 2)

            logger.warning("yfinance 返回无效价格 %s", code)
        except Exception as e:
            logger.error("获取 %s 价格失败: %s", code, e)
        return None

    def calc_target_qty(self, code, weight):
        target_amount = config.TOTAL_CAPITAL * weight
        if target_amount < config.MIN_TRADE_AMOUNT:
            return 0
        price = self.get_price(code)
        if not price:
            return 0
        return int(target_amount / price)

    # ========== 订单 ==========

    def place_order(self, code, qty, side, use_market_order):
        """下单，返回 order_id 或 None"""
        if qty <= 0:
            return None

        if use_market_order:
            order_type = OrderType.MARKET
            order_price = 0.0
            order_desc = "市价单"
        else:
            price = self.get_price(code)
            if price is None:
                return None
            order_type = OrderType.NORMAL
            slippage = config.LIMIT_SLIPPAGE if side == TrdSide.BUY else -config.LIMIT_SLIPPAGE
            order_price = round(price * (1 + slippage), 2)
            order_desc = "限价单 @%s" % order_price

        side_name = _side_name(side)
        logger.info("%s %s x %d (%s)", side_name, code, qty, order_desc)

        ret, data = self._trd_ctx.place_order(
            price=order_price,
            qty=qty,
            code=code,
            trd_side=side,
            order_type=order_type,
            trd_env=self.trd_env,
            fill_outside_rth=not use_market_order,
        )
        if ret != RET_OK:
            logger.error("%s %s 失败: %s", side_name, code, data)
            return None

        order_id = str(data["order_id"].iloc[0])
        logger.info("%s %s x %d 下单成功, order_id=%s", side_name, code, qty, order_id)
        return order_id

    def modify_order_price(self, order_id, code, qty, side, reprice_count):
        """改价重挂，返回是否成功"""
        price = self.get_price(code)
        if price is None:
            return False

        slippage = config.LIMIT_SLIPPAGE + config.REPRICE_SLIPPAGE * reprice_count
        if side == TrdSide.SELL:
            slippage = -slippage
        new_price = round(price * (1 + slippage), 2)

        logger.info("改价 order_id=%s -> @%s (第%d次)", order_id, new_price, reprice_count)
        ret, data = self._trd_ctx.modify_order(
            modify_order_op=ModifyOrderOp.NORMAL,
            order_id=order_id,
            qty=qty,
            price=new_price,
            trd_env=self.trd_env,
        )
        if ret != RET_OK:
            logger.error("改价失败 order_id=%s: %s", order_id, data)
            return False
        return True

    def cancel_order(self, order_id):
        ret, data = self._trd_ctx.modify_order(
            modify_order_op=ModifyOrderOp.CANCEL,
            order_id=order_id,
            qty=0,
            price=0,
            trd_env=self.trd_env,
        )
        if ret != RET_OK:
            logger.warning("撤单失败 order_id=%s: %s", order_id, data)
            return False
        return True

    def order_status(self, order_id):
        """返回 (状态, 已成交数量, 成交均价)，状态为 'filled' / 'pending' / 'failed'"""
        ret, data = self._trd_ctx.order_list_query(order_id=order_id, trd_env=self.trd_env)
        if ret != RET_OK or data.empty:
            return "pending", 0, None
        row = data.iloc[0]
        dealt_qty = int(_num(row.get("dealt_qty")))
        avg_price = _num(row.get("dealt_avg_price")) or None
        status = row["order_status"]
        if status == OrderStatus.FILLED_ALL:
            return "filled", dealt_qty, avg_price
        if status in TERMINAL_STATUSES:
            return "failed", dealt_qty, avg_price
        return "pending", dealt_qty, avg_price

    def wait_orders(self, pending, side):
        """轮询等待订单成交。

        pending: {order_id: {"code", "qty", "market", "checks"}}
        限价单未成交则改价，市价单只等待；超过 MAX_REPRICE_TIMES 次撤单。
        返回 [(code, 成交数量, 成交均价)]，部分成交也计入。
        """
        filled = []
        side_name = _side_name(side)

        while pending:
            time.sleep(config.ORDER_CHECK_INTERVAL)

            for order_id, o in list(pending.items()):
                code = o["code"]
                status, dealt, avg = self.order_status(order_id)

                if status == "pending":
                    o["checks"] += 1
                    if o["checks"] <= config.MAX_REPRICE_TIMES and (
                            o["market"] or self.modify_order_price(order_id, code, o["qty"], side, o["checks"])):
                        continue
                    logger.warning("%s %s 超时未成交, 撤单 order_id=%s", side_name, code, order_id)
                    self.cancel_order(order_id)
                    _, dealt, avg = self.order_status(order_id)
                    notify_order_timeout(side_name, code, o["qty"] - dealt, order_id)
                elif status == "failed" and not dealt:
                    logger.error("%s %s 订单失败, order_id=%s", side_name, code, order_id)

                del pending[order_id]
                if dealt > 0:
                    logger.info("%s %s x %d 已成交, 均价=%s, order_id=%s", side_name, code, dealt, avg, order_id)
                    notify_order_filled(side_name, code, dealt, order_id)
                    filled.append((code, dealt, avg))

        return filled

    # ========== 止损单 ==========

    def sync_stop_orders(self):
        """从账户同步所有未成交的卖出止损单到 _stop_orders。

        不管是程序挂的还是手动挂的，都能感知到。
        """
        self._stop_orders.clear()

        ret, data = self._trd_ctx.order_list_query(trd_env=self.trd_env)
        if ret != RET_OK:
            logger.warning("同步止损单失败: %s", data)
            return

        for _, row in data.iterrows():
            if row["order_status"] in TERMINAL_STATUSES:
                continue
            order_type = str(row["order_type"])
            if row["trd_side"] == TrdSide.SELL and order_type in STOP_ORDER_TYPES:
                self._stop_orders[row["code"]] = str(row["order_id"])
                logger.info("同步到止损单 %s, order_id=%s, 类型=%s", row["code"], row["order_id"], order_type)

        if not self._stop_orders:
            logger.info("账户无未成交止损单")

    def cancel_stop_order(self, code):
        order_id = self._stop_orders.pop(code, None)
        if order_id:
            logger.info("撤销止损单 %s, order_id=%s", code, order_id)
            self.cancel_order(order_id)

    def _maybe_place_stop_loss(self, code, qty, price=None):
        """挂止损，该股票已有止损单则跳过。price 为空时取当前价"""
        if code in self._stop_orders:
            logger.info("跳过挂止损 %s, 已有止损单 order_id=%s", code, self._stop_orders[code])
            return
        price = price or self.get_price(code)
        if price:
            sl_id = place_stop_loss(self._trd_ctx, code, qty, price, self.trd_env)
            if sl_id:
                self._stop_orders[code] = sl_id

    # ========== 跟单执行 ==========

    def execute_diff(self, diff, use_market_order=True, skip_sells=False):
        """根据持仓变化执行跟单交易，先卖后买。

        卖出数量基于跟单持仓记录，不动底仓；买入数量 = 目标股数 - 跟单已持有股数。
        skip_sells: 盘前跳过卖出，只更新跟单记录
        """
        sell_tasks = []
        buy_tasks = []

        # 清仓：只卖跟单记录里的数量，保护底仓
        for pos in diff["removed"]:
            code = pos["code"]
            ct_qty = self._ct_pos.get(code, 0)
            if ct_qty <= 0:
                logger.warning("清仓信号 %s, 但跟单记录为0, 跳过卖出(保护底仓), 请手动处理", code)
                notify_error("清仓信号 %s, 但跟单记录为0, 已跳过自动卖出以保护底仓, 请手动检查" % code)
            elif skip_sells:
                logger.info("盘前跳过卖出 %s, 已从跟单记录删除", code)
                self.update_ct_pos(code, -ct_qty)
            else:
                sell_tasks.append((code, ct_qty))

        # 调仓
        for pos in diff["changed"]:
            code = pos["code"]
            delta = self.calc_target_qty(code, pos["new_weight"]) - self._ct_pos.get(code, 0)
            if delta < 0:
                if skip_sells:
                    logger.info("盘前跳过减仓卖出 %s x %d, 已从跟单记录扣除", code, -delta)
                    self.update_ct_pos(code, delta)
                else:
                    sell_tasks.append((code, -delta))
            elif delta > 0:
                buy_tasks.append((code, delta))

        # 新增
        for pos in diff["added"]:
            code = pos["code"]
            buy_qty = self.calc_target_qty(code, pos.get("weight", 0)) - self._ct_pos.get(code, 0)
            if buy_qty > 0:
                buy_tasks.append((code, buy_qty))

        if sell_tasks:
            self.execute_sells(sell_tasks, use_market_order)
        if buy_tasks:
            self.execute_buys(buy_tasks, use_market_order)

    def execute_sells(self, sell_tasks, use_market_order):
        """批量卖出 [(code, qty)]，卖出前撤销止损单，不超过实际持仓。

        减仓后剩余的跟单仓位重新挂止损。
        """
        my_positions = self.get_my_positions()

        pending = {}
        for code, qty in sell_tasks:
            actual_qty = my_positions.get(code, 0)
            if actual_qty <= 0:
                logger.info("跳过卖出 %s, 实际持仓已为0 (可能已被止损卖出)", code)
                self._stop_orders.pop(code, None)
                self.update_ct_pos(code, -self._ct_pos.get(code, 0))
                continue

            self.cancel_stop_order(code)
            sell_qty = min(qty, actual_qty)
            order_id = self.place_order(code, sell_qty, TrdSide.SELL, use_market_order)
            if order_id:
                pending[order_id] = {"code": code, "qty": sell_qty, "market": use_market_order, "checks": 0}

        for code, qty, _ in self.wait_orders(pending, TrdSide.SELL):
            self.update_ct_pos(code, -qty)

        for code, _ in sell_tasks:
            remaining = self._ct_pos.get(code, 0)
            if remaining > 0:
                self._maybe_place_stop_loss(code, remaining)

    def execute_buys(self, buy_tasks, use_market_order):
        """批量买入 [(code, qty)]，成交后按成交均价挂止损"""
        pending = {}
        for code, qty in buy_tasks:
            order_id = self.place_order(code, qty, TrdSide.BUY, use_market_order)
            if order_id:
                pending[order_id] = {"code": code, "qty": qty, "market": use_market_order, "checks": 0}

        for code, qty, avg_price in self.wait_orders(pending, TrdSide.BUY):
            self.update_ct_pos(code, qty)
            self._maybe_place_stop_loss(code, qty, avg_price)
