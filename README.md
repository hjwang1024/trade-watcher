# Futu Portfolio Watcher

富途牛牛公开组合的调仓监听工具：盯住你关注的组合，一旦建仓、清仓、加减仓，就把变动推送到 iPhone（Bark）。

需要的话也可以打开自动跟单，通过 moomoo OpenD 在自己账户里同步下单。**默认只推送，不下单。**

---

## 推送效果

```
指王之选 仓位变动

【组合收益】
10月收益：-9.8%
总收益：134.0%

【仓位变动】
建仓 NBIS | 仓位：0.0% → 100.0% | 成交价：168.2
清仓 SNDK | 仓位：100.0% → 0.0% | 成交价：无

【持仓明细】
NBIS | 仓位：100.0% | 成本：168.2 | 盈亏：-0.1%
```

会推送的事件：

| 事件 | 说明 |
|------|------|
| 开始监听 | 程序启动时推送一次当前持仓，用来确认推送通道正常 |
| 仓位变动 | 建仓 / 清仓 / 加仓 / 减仓 / 挂单成交 |
| 监听异常 / 恢复 | 连续多次获取数据失败时告警，恢复后再通知 |
| 跟单相关 | 下单成交、超时撤单、止损挂单（仅跟单模式） |

仓位变动以「时效性通知」发送，iPhone 开着专注模式也能收到。

---

## 三分钟上手（只监听）

只监听不需要 moomoo 账户，也不需要安装 OpenD。

**1. 安装依赖**（Python 3.10+）

```bash
pip3 install -r requirements.txt
```

**2. 填写 Bark key**

在 iPhone 上安装 [Bark](https://apps.apple.com/app/bark-customed-notifications/id1403753865)，打开 App 复制推送地址 `https://api.day.app/<key>/...` 里的 `<key>`。然后：

```bash
cp local_config.example.py local_config.py
# 编辑 local_config.py，填入 BARK_KEY = "<key>"
```

**3. 选择组合并启动**

在 `config.py` 中把 `PORTFOLIO_ID` 改成你想跟踪的组合 ID（富途网页版组合页面链接中的数字），然后：

```bash
python3 positions.py      # 先看一眼组合当前持仓
python3 main.py --once    # 检查一次并推送「开始监听」，确认手机能收到
python3 main.py           # 持续监听
```

后台长期运行（macOS，防止空闲休眠）：

```bash
nohup caffeinate -i python3 main.py > /dev/null 2>&1 &   # 启动
tail -f data/copytrade.log                                # 看日志
pkill -f "python3 main.py"                                # 停止
```

---

## 配置

配置拆成两个文件，避免把密码提交到仓库：

| 文件 | 内容 | 是否提交 |
|------|------|----------|
| `config.py` | 组合 ID、轮询间隔、跟单参数等普通配置 | ✅ |
| `local_config.py` | Bark key、交易密码、邮箱等私密配置，同名项覆盖 `config.py` | ❌ 已在 `.gitignore` |

常用配置项：

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `PORTFOLIO_ID` | `"183730"` | 要监听的公开组合 ID |
| `POLL_INTERVAL` | `15` | 轮询间隔（秒）。太小可能被富途限流，失败时会自动拉长间隔 |
| `WEIGHT_CHANGE_THRESHOLD` | `0.02` | 仓位变化超过 2% 才算调仓，过滤股价波动 |
| `FAILURE_ALERT_THRESHOLD` | `10` | 连续失败多少次推送告警 |
| `BARK_KEY` | — | Bark 推送 key（写在 `local_config.py`） |
| `BARK_SERVER` | `https://api.day.app` | 自建 Bark 服务时改成自己的地址 |
| `BARK_GROUP` / `BARK_SOUND` | — | 通知分组、铃声 |
| `EMAIL_ENABLED` | `False` | 是否同时发邮件（SMTP，需要在 `local_config.py` 填邮箱和授权码） |
| `COPYTRADE_ENABLED` | `False` | 是否自动跟单，见下文 |

---

## 自动跟单（可选）

> ⚠️ 自动交易可能造成真实亏损。请先在模拟盘充分测试，自行承担全部风险。

### 准备

1. 下载并启动 [moomoo OpenD](https://www.moomoo.com/download/openD)，扫码登录（默认端口 11111），运行期间保持开启
2. 签署 [OpenAPI 风险披露协议](https://risk-disclosure.us.moomoo.com/index?agreementNo=USOT0027)，否则 API 无法交易
3. 在 `local_config.py` 中填写：

   ```python
   TRADE_PASSWORD = "123456"   # moomoo 交易解锁密码
   # TRADE_ENV = "REAL"        # 默认 SIMULATE 模拟盘，验证无误后再切实盘
   ```

4. 在 `config.py` 中打开 `COPYTRADE_ENABLED = True`，按需调整 `TOTAL_CAPITAL`（跟单资金）、止损等参数
5. 检查能否连上账户：`python3 positions.py --account`

### 运行

```bash
python3 main.py --dry     # 干跑：完整流程但不下单，建议先跑一段时间
python3 main.py           # 正式跟单
```

启动时会逐只询问组合中的股票：

- **账户已持有**：输入其中多少股算「跟单仓位」，剩下的视为底仓，程序永远不会自动卖出底仓
- **账户未持有**：选择是否买入；夜盘时会记下来，等盘前自动执行

### 跟单规则

| 时段（美东） | 行为 |
|------|------|
| 盘前 4:00–9:30 | 限价单买入；卖出按 `PREMARKET_SELL_MODE` 决定 |
| 盘中 9:30–16:00 | 市价单买卖 |
| 盘后 16:00–20:00 | 限价单买卖 |
| 夜盘 20:00–4:00、周末 | 只推送，不下单；记录账户持仓，盘前对账后再执行 |

- **下单数量**：`TOTAL_CAPITAL × 目标仓位 ÷ 现价`，低于 `MIN_TRADE_AMOUNT` 不下单
- **限价单追单**：没成交就逐次加价改单，超过 `MAX_REPRICE_TIMES` 次后撤单，部分成交的照常记录
- **止损**：买入成交后按成交均价自动挂止损，`STOP_LOSS_MODE` 可选 `none` / `fixed` / `trailing`
- **盘前对账**：夜盘到盘前之间你手动买卖过的股票，会自动补录到跟单记录

价格取自 yfinance；交易时段判断不含美股节假日。

---

## 工作原理

```
           ┌──────────── 每 POLL_INTERVAL 秒 ────────────┐
           ▼                                             │
  富途组合接口 ──► 与上一次持仓对比 ──► 有变化？──否─────┘
                                         │是
                         ┌───────────────┴───────────────┐
                         ▼                               ▼
                  Bark / 邮件推送              跟单模块（若开启）
                                              moomoo OpenD 下单
```

- 每次都和**上一次**抓到的持仓比较，股价波动带来的仓位漂移不会累积成误报
- 快照保存在 `data/`，重启后能补报停机期间发生的调仓
- 月收益由程序自己记录月初净值计算；首次运行的当月没有基准，显示「统计中」

---

## 目录结构

```
├── main.py                   入口：轮询循环、失败退避
├── positions.py              命令行查看组合 / 账户持仓
├── monitor.py                抓取组合数据、对比变化、月收益统计（只读）
├── notify.py                 Bark / 邮件推送与消息模板
├── copytrade.py              跟单流程：初始化、夜盘延后、盘前对账
├── trader.py                 OpenD 交易封装：下单、追单、撤单、查询
├── stoploss.py               止损单
├── market_hours.py           美股交易时段
├── storage.py                data/ 下状态文件读写
├── config.py                 普通配置
├── local_config.example.py   私密配置模板
└── data/                     运行时生成：快照、跟单记录、净值、日志
```

---

## 常见问题

**手机收不到推送？**
先运行 `python3 main.py --once`，看日志里是否有「Bark 已推送」。如果显示「未配置 BARK_KEY」，检查 `local_config.py` 是否存在、key 是否正确。

**日志里一直是「请求组合数据失败」？**
可能是请求太频繁被富途限流，把 `POLL_INTERVAL` 调大一些；程序也会自动拉长重试间隔。

**运行时日志很少，程序还活着吗？**
没有变化时不会写日志。看 `data/watch_snapshot.json` 的修改时间是否在一个轮询间隔以内即可。

**跟单提示「解锁交易失败」或「No one available account」？**
确认 OpenD 已登录、已签署 API 协议、`TRADE_PASSWORD` 填写正确。

**怎么保护已有的持仓不被卖掉？**
跟单初始化时，已持有股票的「跟单数量」填小于实际持仓的数，差额就是底仓，不会被自动卖出。

---

## 项目来源与许可证

本项目基于开源项目 futu_copytrade（GPL-3.0）修改而来，感谢原作者。2026 年 10 月在原有跟单功能的基础上做了重构，主要改动：

- 监听与跟单拆分，新增只监听模式并设为默认
- 新增 Bark 推送与仓位报告模板，新增组合收益统计
- 私密配置独立到 `local_config.py`
- 修复多处跟单逻辑问题：待买入不执行、干跑仍会下单、成交确认、超时撤单等

本项目同样以 [GNU GPL v3.0](LICENSE) 发布。

## 免责声明

本项目仅供学习交流，不构成任何投资建议。组合数据来自富途公开页面，可能延迟或不准确。使用自动跟单功能产生的一切交易风险和损失由使用者自行承担。
