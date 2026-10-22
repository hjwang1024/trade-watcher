"""配置文件

密码、Bark key 等私密信息不要写在这里，写到 local_config.py（已加入 .gitignore，不会提交）。
local_config.py 里的同名配置会覆盖本文件，参考 local_config.example.py。
"""

# ========== 运行模式 ==========
# 是否自动跟单下单（默认关闭）
#   False - 只监听组合持仓变化并推送 Bark 通知，不连接 OpenD、不下单
#   True  - 监听 + 自动跟单，需要 moomoo OpenD 运行并登录
COPYTRADE_ENABLED = False

# ========== 监控目标 ==========
# 公开模拟组合 ID
PORTFOLIO_ID = "183730"

# 轮询间隔（秒）。太小可能触发富途接口限流，请求失败时会自动退避
POLL_INTERVAL = 15

# ========== OpenD 连接 ==========
OPEND_HOST = "127.0.0.1"
OPEND_PORT = 11111

# ========== 交易账户（仅跟单模式使用）==========
# 先用模拟盘测试，测试通过后改为真实账户
# TrdEnv.SIMULATE = 模拟, TrdEnv.REAL = 真实
TRADE_ENV = "SIMULATE"

# 交易解锁密码（写在 local_config.py）
TRADE_PASSWORD = ""

# 交易市场: US(美股), HK(港股)
TRADE_MARKET = "US"

# ========== 跟单策略 ==========
# 跟单资金（美元），用于计算每只股票买多少股
TOTAL_CAPITAL = 10000.0

# 最小交易金额（美元），低于此金额不跟单
MIN_TRADE_AMOUNT = 100.0

# 权重变化阈值，低于此值视为股价波动忽略，0.02 = 2%
WEIGHT_CHANGE_THRESHOLD = 0.02

# 盘前卖出模式:
#   "always"     - 盘前正常跟随卖出信号卖出
#   "never"      - 盘前只买不卖，卖出信号只更新跟单记录
#   "same_count" - 盘前只有5只→5只换仓时不卖，其他情况正常卖出
PREMARKET_SELL_MODE = "same_count"

# ========== 富途网页端 Cookie ==========
# 从浏览器开发者工具中获取，用于访问公开组合数据
# 打开 futunn.com 登录后，F12 -> Application -> Cookies -> 复制全部
FUTU_COOKIE = ""  # 写在 local_config.py

# ========== Bark 推送 ==========
BARK_ENABLED = True
BARK_SERVER = "https://api.day.app"   # 自建服务器改成自己的地址
BARK_KEY = ""                         # 写在 local_config.py
BARK_GROUP = "富途跟单"                # 通知分组
BARK_SOUND = ""                       # 铃声，留空用默认
# 连续请求失败多少次推送一次告警（恢复后也会推送）
FAILURE_ALERT_THRESHOLD = 10

# ========== 邮件通知（可选）==========
EMAIL_ENABLED = False
EMAIL_SMTP_HOST = "smtp.gmail.com"
EMAIL_SMTP_PORT = 465
EMAIL_SENDER = ""
EMAIL_PASSWORD = ""  # Gmail 应用专用密码
EMAIL_RECEIVER = ""

# ========== 限价单滑点 ==========
# 限价单初始滑点比例，0.001 = 0.1%
LIMIT_SLIPPAGE = 0.001
# 每次改价增加的滑点比例
REPRICE_SLIPPAGE = 0.001
# 检查订单状态的间隔（秒）
ORDER_CHECK_INTERVAL = 10
# 最大改价次数
MAX_REPRICE_TIMES = 5

# ========== 止损 ==========
# 止损模式: "none" 不挂止损, "fixed" 固定比例止损, "trailing" 跟踪止损
STOP_LOSS_MODE = "trailing"

# 固定止损比例（仅 fixed 模式），0.033 = 跌 3.3% 触发（decimal 乘数，用于 stoploss.py 计算）
STOP_LOSS_RATIO = 0.033

# 跟踪止损参数（仅 trailing 模式）
TRAIL_TYPE = "ratio"    # "ratio" 按比例回撤, "amount" 按金额回撤
TRAIL_VALUE = 3         # 回撤比例(3=3%) 或 回撤金额(美元)

# ========== 日志 ==========
LOG_FILE = "data/copytrade.log"

# ========== 本地私密配置覆盖 ==========
try:
    from local_config import *  # noqa: F401,F403
except ImportError:
    pass
