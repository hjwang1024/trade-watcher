"""
本地私密配置模板

复制为 local_config.py 后填写，local_config.py 已加入 .gitignore 不会提交。
这里的配置会覆盖 config.py 中的同名项，只需写要改的。
"""

# Bark 推送 key：Bark App 里推送地址 https://api.day.app/<key>/... 中的 <key>
BARK_KEY = ""

# ---- 以下仅跟单模式需要 ----
# TRADE_ENV = "REAL"          # 实盘，默认 SIMULATE 模拟盘
# TRADE_PASSWORD = "123456"   # moomoo 交易解锁密码

# ---- 邮件通知（可选）----
# EMAIL_ENABLED = True
# EMAIL_SENDER = "your_email@gmail.com"
# EMAIL_PASSWORD = "your_app_password"
# EMAIL_RECEIVER = "your_email@gmail.com"

# ---- 富途 Cookie（可选，公开组合一般不需要）----
# FUTU_COOKIE = ""
