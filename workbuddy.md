# WorkBuddy 开发上下文记录

本文件记录本项目在 **WorkBuddy** 中完成创意构思、MCP 联调、异常定位与代码实现的真实过程，用于核验参赛项目确实使用腾讯 WorkBuddy 开发。

- 工具：WorkBuddy（Agent 模式）
- 日期：2026-10-18
- 工作目录：`McSentry`
- 说明：以下为会话上下文的**整理导出**（按主题归并，非逐字全量日志）。所有结论均来自当次真实工具调用，未事后补写。

---

## 一、立项阶段

### 用户输入（第 1 轮）

> 先读取 https://github.com/M-China/mcd-developer-innovation-challenge 的内容，我想参赛

**WorkBuddy 动作**：抓取活动仓库 README、活动规则 `activityGuidelines.md`、`CONTEST_DECLARATION.md`、实时排行榜 `RANKING.md`、MCP Server 使用指南。

**关键判断（由 AI 提出）**：

1. 截止 2026-10-25 23:59，当时仅剩 7 天
2. 排名完全由公开 Star 决定，前 100 名进榜
3. 当时榜尾为 15★，前三名分别为 534 / 490 / 400 ★
4. 结论：目标应定在「进前 100」，而非冲前三——前三名是存量社区势能，7 天追不上

### 用户输入（第 2 轮）

用户提出自己的选题：**麦麦账户哨兵 McAccount Sentinel**，一个基于定时任务 + 微信主动推送 + 多 MCP 工具编排的账户健康巡检系统。理由是"省钱、营养、点餐助手、人格测试已非常拥挤，而账户资产到期是空白"。

**WorkBuddy 的核查与修正**（这一轮扭转了方案）：

| 核查项 | 结论 |
|---|---|
| `wechat-clawbot-push` 连接器是否存在 | **不存在**。检索连接器市场，与微信相关的仅有「言序」（写公众号文章推草稿箱）与「观思动」（建企业应用），均不能做个人微信主动推送 |
| "资产到期是空白"是否成立 | **不成立**。排行榜已有 `mcd-expiry-butler`(30★)、`mcd-points-steward`(20★)、`mcd-points-vault`(18★)、`mcd-points-actuary`(17★) 四个同题材项目 |
| MCP 配置状态 | `~/.workbuddy/mcp.json` 已存在但 `Authorization` 缺 `Bearer ` 前缀，调用必然 401 |

**据此提出的方案修正**：

1. 差异化从"扫描+清单"改为"**报告型 → 执行型**"：`auto-bind-coupons` 是唯一的零风险写操作，可以做到"券已经领好了"，而不是"提醒你券要过期"
2. 推送通道改为**可插拔**，默认邮件（真实可用），企业微信/公众号作为扩展
3. 补上此前遗漏的 `mcp-config.example.json`

### 用户输入（第 3 轮）

> 可以，MCP已经启用了

---

## 二、MCP 联调阶段（真实数据驱动设计）

WorkBuddy 先修好 `Bearer ` 前缀，随后对真实 MCP 逐工具实测。**这一步直接决定了产品形态**。

### 实测得到的真实账户画像

来自 `query-my-account` 原始返回：

| 字段 | 值 |
|---|---:|
| `accumulativePoint` | 778.5 |
| `expiredPoint` | **758.5** |
| `availablePoint` | 0 |
| `usedPoint` | 20 |

过期占比 **97.4%**。这个数字后来成了 README 的开篇论据——它比任何"痛点描述"都有说服力。

### 三个真实异常（联调中发现，非预设）

**① `available-coupons` 稳定报 `-32602`**

```
MCP error -32602: Structured content does not match the tool's output schema:
data/data must be array
```

重复 3 次均复现。WorkBuddy 提出假设：这是**客户端 schema 校验**问题而非服务端故障。随后用自写的零依赖 MCP 客户端绕过 SDK 校验，直接读 `content[].text` —— **成功拿到数据**，证实假设：服务端在"无可领券"时返回 `data: null`，与 `outputSchema` 的 `type: array` 冲突。**空结果反而报错，有券时不会。**

**② `mall-points-products` 的积分券 `price` 恒为 `"0"`**

```json
{ "spuName": "10.9元指定小食任选1份", "point": "100", "price": "0" }
```

首版按 `price > 0` 过滤 → 整个积分商城样本被筛光 → 报告输出"未取得积分兑换汇率"，汇率永远算不出。修复：`price <= 0` 时从 `spuName` 用正则回退解析面额。

**③ 同一 Server 混用三种响应形态**

`query-lottery-info` 与 `mall-points-products` 返回的是「`# API Response Information` 说明壳 + `## Original Response` 内嵌 JSON」，而 `query-my-account` 是纯 JSON、`query-my-coupons` 是纯 Markdown。首版解析器按"找第一个 `[` 或 `{`"实现，被说明文字里的 `data[].spuName` 方括号带偏，**两个工具双双静默返回 `None`**——而其他工具正常，极具迷惑性。修复：改用 `json.JSONDecoder.raw_decode()` 扫描所有起点取最长可解码跨度。

**④ 附带发现：商城存在过期商品**

部分 `status = 2` 的商品 `downTime` 已是历史时间。不过滤会虚高汇率——实测那个 `¥0.188/分`（18.8元板烧两件套/100积分）其实来自一件 3 月已下架商品，剔除后最优汇率回落至 `¥0.138/分`。

### 由真实数据推导出的产品指标

用商城 7 件当前可兑换商品反推积分汇率：

| 口径 | 元/积分 |
|---|---:|
| 最优 | 0.138 |
| 常规（中位数） | 0.055 |
| 最差 | 0.027 |

**同样 800 积分，换 16 份派值 ¥110，换 1 份巨无霸组合只值 ¥21.9。** 这个 5 倍差距成为报告固定输出项。

---

## 三、实现与迭代阶段

### 产出

| 文件 | 说明 |
|---|---|
| `sentry/mcp_client.py` | 零依赖 MCP 客户端（Streamable HTTP + JSON-RPC + SSE 解析 + 协议版本降级） |
| `sentry/normalize.py` | 三形态归一化，含 `raw_decode` 扫描与 markdown 表格解析 |
| `sentry/collect.py` | 9 工具采集，带逐项溯源（`Probe`），失败不污染统计 |
| `sentry/valuate.py` | 汇率推导、估值、排序，纯确定性无 LLM |
| `sentry/report.py` | 长报告 + 推送稿双渲染 |
| `sentry/pushers/` | 可插拔通道：console / file / email |
| `mcsentry.py` | CLI：`patrol` / `probe` / `version` |
| `fixtures/demo_snapshot.json` | 无 Token 演示数据 |

### 迭代中修正的两个设计错误

**错误 1：排序混用了不可比的"紧迫度"**

首版给每类资产算 `value/hours` 作为紧迫度统一排序。结果一张明天到期的 ¥3 券排在一笔两周后清零的 ¥88 积分前面——技术上成立，实际无用。改为**双主键**：先分「会被清零」与「零成本可做」，前者内部按到期时间、再按金额。

**错误 2：结论句抓错重点**

首版结论句只报"最近到期的资产"，于是输出"3 天内有约 ¥3.00 的资产会清零"，而真正的风险是 ¥88 积分。改为**总额优先 + 点名最大一笔**："到 11-30 为止，你有 3 项资产共约 ¥132.72 会清零；最大一笔是「本月将过期：640 积分」，13 天后到期。"

### 验证

- `python mcsentry.py patrol --demo` — 构造数据跑通全链路
- `python mcsentry.py patrol --full` — 真实账户出报告，9 个只读工具**全部调用成功**（0 失败），其中 5 项有数据、4 项为真实空态（券列表、可领券、奖品、订单均为空，属于账户本身状态而非故障）
- `python mcsentry.py probe` — 9/9 只读工具连通
- `python mcsentry.py patrol --json out/snapshot.json --push file` — 快照落盘与文件通道验证通过，且确认 `accountId` 已脱敏

---

## 四、WorkBuddy 在本项目中的角色

| 环节 | 由 WorkBuddy 完成 |
|---|---|
| 活动规则解读与竞争分析 | ✅ 抓取 README/规则/排行榜并给出冲榜策略 |
| **证伪用户假设** | ✅ 查证 `wechat-clawbot-push` 不存在、同题材项目已存在 |
| MCP 配置排障 | ✅ 发现缺少 `Bearer ` 前缀 |
| 异常根因定位 | ✅ 用自写客户端验证"是客户端校验而非服务端故障"的假设 |
| 代码实现 | ✅ 全部 Python 代码 |
| 文档撰写 | ✅ README / MCP_INTEGRATION / SKILL.md |
| 参赛声明 | 直接使用官方原文，未改动（哈希比对确认一致） |

**未交由 AI 决定的部分**：选题方向（用户提出）、是否参赛、是否开启写操作权限、最终发布与传播。
