"""
通知模块：Bark 推送 + 邮件（可选）
"""

import logging
import smtplib
import time
from email.mime.text import MIMEText

import requests

import config

logger = logging.getLogger(__name__)

ERROR_NOTIFY_COOLDOWN = 30 * 60


def send_bark(title: str, body: str, level: str = "active"):
    """Bark 推送。level: active(默认) / timeSensitive(专注模式下也提醒) / passive(静默)"""
    if not config.BARK_ENABLED:
        return
    if not config.BARK_KEY:
        logger.warning("未配置 BARK_KEY，跳过推送")
        return

    payload = {
        "device_key": config.BARK_KEY.strip(),
        "title": title,
        "body": body,
        "group": config.BARK_GROUP,
        "level": level,
    }
    if config.BARK_SOUND:
        payload["sound"] = config.BARK_SOUND

    try:
        resp = requests.post(f"{config.BARK_SERVER.rstrip('/')}/push", json=payload, timeout=10)
        result = resp.json()
        if result.get("code") != 200:
            raise RuntimeError(result.get("message") or resp.text)
        logger.info("Bark 已推送: %s", title)
    except Exception as e:
        logger.error("Bark 推送失败: %s", e)


def send_email(subject: str, body: str):
    if not config.EMAIL_ENABLED:
        return

    msg = MIMEText(body, "plain", "utf-8")
    msg["From"] = config.EMAIL_SENDER
    msg["To"] = config.EMAIL_RECEIVER
    msg["Subject"] = subject

    try:
        with smtplib.SMTP_SSL(config.EMAIL_SMTP_HOST, config.EMAIL_SMTP_PORT, timeout=15) as server:
            server.login(config.EMAIL_SENDER, config.EMAIL_PASSWORD)
            server.sendmail(config.EMAIL_SENDER, config.EMAIL_RECEIVER, msg.as_string())
        logger.info("邮件已发送: %s", subject)
    except Exception as e:
        logger.error("邮件发送失败: %s", e)


def send(title: str, body: str, level: str = "active"):
    """发送到所有已开启的通知渠道"""
    send_bark(title, body, level)
    send_email(title, body)


# ========== 组合监听 ==========

def _ticker(p: dict) -> str:
    return p["code"].split(".", 1)[-1]


def _pct(x: float) -> str:
    return f"{x:.1%}"


def _price(x: float) -> str:
    if not x:
        return "无"
    return f"{x:.1f}" if x >= 100 else f"{x:.2f}"


def format_positions(positions: list[dict]) -> list[str]:
    if not positions:
        return ["空仓"]
    return [
        f"{_ticker(p)} | 仓位：{_pct(p['weight'])} | 成本：{_price(p.get('cost_price'))} | 盈亏：{_pct(p.get('pl_ratio', 0))}"
        + (" | 挂单中" if p.get("is_pending") else "")
        for p in positions
    ]


def format_diff(diff: dict) -> list[str]:
    def deal_price(p):
        return "挂单中" if p.get("is_pending") else _price(p.get("cost_price"))

    lines = []
    for p in diff["added"]:
        lines.append(f"建仓 {_ticker(p)} | 仓位：0.0% → {_pct(p['weight'])} | 成交价：{deal_price(p)}")
    for p in diff["removed"]:
        lines.append(f"清仓 {_ticker(p)} | 仓位：{_pct(p['weight'])} → 0.0% | 成交价：无")
    for p in diff["changed"]:
        action = "加仓" if p["new_weight"] > p["old_weight"] else "减仓"
        price = "挂单中" if p.get("is_pending") else "无"
        lines.append(f"{action} {_ticker(p)} | 仓位：{_pct(p['old_weight'])} → {_pct(p['new_weight'])} | 成交价：{price}")
    for p in diff["filled"]:
        lines.append(f"成交 {_ticker(p)} | 仓位：{_pct(p['weight'])} | 成交价：{deal_price(p)}")
    return lines


def format_report(summary: dict | None, positions: list[dict], diff: dict | None = None) -> str:
    """组合收益 + 仓位变动 + 持仓明细"""
    sections = []
    if summary:
        month_return = summary["month_return"]
        sections.append("【组合收益】\n"
                        f"{summary['month']}月收益：{'统计中' if month_return is None else _pct(month_return)}\n"
                        f"总收益：{_pct(summary['total_return'])}")
    if diff:
        sections.append("【仓位变动】\n" + "\n".join(format_diff(diff)))
    sections.append("【持仓明细】\n" + "\n".join(format_positions(positions)))
    return "\n\n".join(sections)


def notify_changes(portfolio_name: str, summary: dict | None, diff: dict, positions: list[dict]):
    send(f"{portfolio_name} 仓位变动", format_report(summary, positions, diff), level="timeSensitive")


def notify_startup(portfolio_name: str, summary: dict | None, positions: list[dict]):
    mode = "自动跟单" if config.COPYTRADE_ENABLED else "只通知，不下单"
    send(f"{portfolio_name} 开始监听", format_report(summary, positions) + f"\n\n模式：{mode}")


def notify_monitor_down(portfolio_name: str, failures: int):
    send(f"{portfolio_name} 监听异常",
         f"连续 {failures} 次获取组合持仓失败，可能被限流或网络异常，程序会继续重试",
         level="timeSensitive")


def notify_monitor_recovered(portfolio_name: str):
    send(f"{portfolio_name} 监听恢复", "组合持仓获取已恢复正常")


# ========== 跟单交易 ==========

def notify_deferred(portfolio_name: str):
    send(f"{portfolio_name} 跟单待执行",
         "夜盘时段(20:00-4:00 ET)不自动下单，将在盘前自动执行；如需提前操作请手动处理")


def notify_order_filled(side: str, code: str, qty: int, order_id: str):
    send(f"跟单 {side}成交 {code} x {qty}", f"{side}成交\n股票: {code}\n数量: {qty}\n订单号: {order_id}")


def notify_order_timeout(side: str, code: str, qty: int, order_id: str):
    send(f"跟单警告 {code} {side}超时",
         f"{side}超时未成交，已撤单，请手动处理\n股票: {code}\n未成交数量: {qty}\n订单号: {order_id}",
         level="timeSensitive")


def notify_stop_loss_placed(code: str, qty: int, mode: str, order_id: str, detail: str = ""):
    mode_name = "固定止损" if mode == "fixed" else "跟踪止损"
    body = f"{mode_name}已挂单\n股票: {code}\n数量: {qty}\n订单号: {order_id}"
    if detail:
        body += "\n" + detail
    send(f"跟单 {code} x {qty} 已挂{mode_name}", body)


def notify_stop_loss_failed(code: str, qty: int, reason: str = ""):
    body = f"止损单挂单失败，请手动处理\n股票: {code}\n数量: {qty}"
    if reason:
        body += "\n原因: " + reason
    send(f"跟单警告 {code} 止损挂单失败", body, level="timeSensitive")


_last_error = {"msg": None, "time": 0.0}


def notify_error(error_msg: str):
    """同样的错误 30 分钟内只推送一次，避免持续故障时刷屏"""
    now = time.time()
    if error_msg == _last_error["msg"] and now - _last_error["time"] < ERROR_NOTIFY_COOLDOWN:
        return
    _last_error.update(msg=error_msg, time=now)
    send("跟单程序异常", f"运行出错，请检查:\n\n{error_msg}", level="timeSensitive")
