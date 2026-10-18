# MCP 集成说明

本文说明麦麦账户哨兵**实际调用**的麦当劳 MCP 能力、调用顺序、每个工具承担的业务职责，以及在真实联调中发现的响应异常与对应处理。所有结论来自 2026-10-18 对 `https://mcp.mcd.cn` 的真实调用记录，非推测。

---

## 1. 接入信息

| 项目 | 值 |
|---|---|
| MCP Server | 麦当劳中国 MCP Server（远程托管） |
| 接入地址 | `https://mcp.mcd.cn` |
| 传输协议 | Streamable HTTP（JSON-RPC 2.0） |
| 协商协议版本 | `2025-06-18`（客户端按 2025-06-18 → 2025-03-26 → 2024-11-05 依次降级重试） |
| 鉴权 | 请求头 `Authorization: Bearer <MCP_TOKEN>`，Token 由个人在 `open.mcd.cn/mcp` 申请 |
| 会话 | `initialize` 响应头返回 `Mcp-Session-Id`，后续请求携带 |
| 服务端工具总数 | 35（`tools/list` 实测，官方文档列 33，已随版本迭代增加） |
| 限流 | 每 Token 600 请求/分钟，超限返回 429 |

客户端实现见 [`sentry/mcp_client.py`](sentry/mcp_client.py)，**零第三方依赖**（仅标准库 `urllib`），可直接在无 pip 环境的服务器或 CI 中运行。

---

## 2. 使用的 Tool 清单

哨兵全部能力只依赖以下 9 个工具。**标「只读」的 8 个是默认路径；唯一的写操作 `auto-bind-coupons` 必须显式开启。**

| # | Tool | 读/写 | 在哨兵中的职责 |
|---:|---|---|---|
| 1 | `now-time-info` | 只读 | **时间基准**。所有「还剩几小时」都以服务器时间为准，避免本机时钟漂移导致误判临期 |
| 2 | `query-my-account` | 只读 | **积分资产主账本**。取 `availablePoint` / `currentMouthExpirePoint` / `nextMouthExpirePoint` / `expiredPoint` / `accumulativePoint`，是唯一能拿到"本月/下月将过期积分"的来源 |
| 3 | `query-my-coupons` | 只读 | **已持有券及有效期**。已领取待核销的券在这里，是"券快过期"告警的数据源 |
| 4 | `available-coupons` | 只读 | **未领取的券**。麦麦省待领券，对应可自动执行的零风险动作 |
| 5 | `query-lottery-info` | 只读 | **抽奖资源**。取剩余次数与 `drawDecision`，判断"有免费次数没用"这类隐性浪费 |
| 6 | `query-my-prizes` | 只读 | **待领取奖品**。中奖但未领的奖品同样会过期 |
| 7 | `order-list` | 只读 | **消费节奏**。用于判断账户是否活跃，避免对僵尸账户做无意义告警 |
| 8 | `mall-points-products` | 只读 | **积分定价锚点**。用商城真实商品反推"1 积分值多少钱"，让金额估算有据可依 |
| 9 | `auto-bind-coupons` | **写** | **唯一自动执行的动作**。一键领取所有可领券，不产生交易、不花钱，风险为零 |

---

## 3. 调用流程

一次完整巡检是「时间校准 → 全量发现 → 估值 → 排序 → 触达 → 可选执行」六步。前五步纯只读。

```
                    ┌──────────────────┐
                    │ now-time-info    │  校准服务器时间
                    └────────┬─────────┘
                             │
        ┌────────────────────┼────────────────────┬──────────────┐
        │                    │                    │              │
┌───────▼────────┐  ┌────────▼────────┐  ┌────────▼───────┐  ┌───▼──────────────┐
│ query-my-      │  │ query-my-       │  │ available-     │  │ query-lottery-   │
│ account        │  │ coupons         │  │ coupons        │  │ info             │
│ 积分/过期桶    │  │ 持有券+有效期   │  │ 待领券         │  │ 剩余次数         │
└───────┬────────┘  └────────┬────────┘  └────────┬───────┘  └───┬──────────────┘
        │                    │                    │              │
        │           ┌────────▼────────┐  ┌────────▼─────────┐    │
        │           │ query-my-prizes │  │ order-list       │    │
        │           │ 待领奖品        │  │ 活跃度           │    │
        │           └────────┬────────┘  └────────┬─────────┘    │
        │                    │                    │              │
        └────────────────────┴─────────┬──────────┴──────────────┘
                                       │
                          ┌────────────▼─────────────┐
                          │ mall-points-products     │  积分 → 人民币汇率
                          │ （可缓存，非每次必调）   │
                          └────────────┬─────────────┘
                                       │
                          ┌────────────▼─────────────┐
                          │ 确定性估值 + 排序（本地）│  无 LLM 参与
                          └────────────┬─────────────┘
                                       │
                     ┌─────────────────┴──────────────────┐
                     │                                    │
            ┌────────▼─────────┐                ┌─────────▼──────────┐
            │ 推送通道          │                │ --execute-claims   │
            │ console/file/email│                │ auto-bind-coupons  │
            │ （可插拔）        │                │ （写，需显式授权） │
            └──────────────────┘                └────────────────────┘
```

对应代码：

- 采集：[`sentry/collect.py`](sentry/collect.py) · `collect()`
- 归一化：[`sentry/normalize.py`](sentry/normalize.py) · `extract_json()`
- 估值/排序：[`sentry/valuate.py`](sentry/valuate.py) · `assess()`
- 渲染：[`sentry/report.py`](sentry/report.py)
- 编排：[`mcsentry.py`](mcsentry.py) · `cmd_patrol()`

---

## 4. 业务价值：这个工具为什么不能省

| Tool | 不用它会怎样 |
|---|---|
| `query-my-account` | 无法知道"本月/下月各有多少积分要清零"——这是整个产品的核心指标，没有任何替代来源 |
| `query-my-coupons` | 只能看到"可领的券"，看不到"已领但要过期的券"，而后者才是真金白银的损失 |
| `available-coupons` | 看到损失却无法挽回。它是"从提醒到代办"的唯一入口 |
| `mall-points-products` | 所有金额无从谈起，只能含糊地说"你有 640 积分要过期"，说服力为零 |
| `query-lottery-info` | 漏掉免费抽奖次数这类"零成本未使用资源" |
| `query-my-prizes` | 漏掉中奖未领，这是最冤的一种损失 |
| `now-time-info` | 本机时钟一旦偏差，临期判断全错；且麦当劳时间戳均为 UTC+8 本地时间 |
| `order-list` | 无法区分"活跃账户"和"僵尸账户"，会对从不下单的人做无效告警 |
| `auto-bind-coupons` | 产品退化成"又一个清单工具"，与已有的同类项目没有区别 |

---

## 5. 实测异常与处理（核心工程价值）

把 MCP 工具直接串联"应该就能用"是错觉。以下是真实联调中撞到的三个问题，**它们决定了本项目必须存在一个归一化层**。

### 5.1 `available-coupons`：空结果必然触发 `-32602`

**现象**：通过标准 MCP 客户端调用时稳定报错：

```
MCP error -32602: Structured content does not match the tool's output schema:
data/data must be array
```

**复现率**：3/3，与账户状态无关，只与"当前没有可领券"有关。

**根因**（读原始响应确认）：该工具的 `outputSchema` 声明 `data` 为 array，但当会员没有可领券时，服务端返回的是

```json
{ "success": false, "code": 499, "message": "No coupons available", "data": null }
```

`data: null` 不满足 `type: array`。也就是说——**恰好在"没有东西可领"这个最常见的情况下，schema 校验失败**。合法 Schema 客户端会把这个当硬错误抛出，丢失"确实没有券"这个有效信息。

**处理**：哨兵不走 SDK 的 structuredContent 校验，而是直读 `content[].text` 并用 `extract_json()` 解析（见 `sentry/mcp_client.py` 模块注释）。实测同一 Token 下直读可正常拿到 `data: null` 与 `code: 499`，从而正确判定"无可领券"，而不是报错中断整轮巡检。

> 这是一处**可被官方顺手修复**的问题：把 `outputSchema.data` 改为 `["array", "null"]`，或空态返回 `data: []`。

### 5.2 `mall-points-products`：积分券的 `price` 恒为 `"0"`

**现象**：所有"到店专用"积分兑换商品，`price` 字段都是 `"0"`。

```json
{ "spuName": "10.9元指定小食任选1份", "point": "100", "price": "0", "catName": "到店专用" }
```

**后果**：若按 `price > 0` 过滤可兑换商品（这是最自然的写法），**整个积分商城样本会被全部筛掉**，积分汇率永远算不出来——哨兵第一版就是这样静默失败的：报告里写着"未取得积分兑换汇率"。

**处理**：`price <= 0` 时回退到从 `spuName` 解析面额（正则 `(\d+(?:\.\d+)?)\s*元`），见 `sentry/collect.py` 的 `_face_value()`。同时要求 `point > 0`，排除"品鉴会/派对"这类 `price > 0` 但 `point = 0` 的现金商品。

### 5.3 同一 Server 混用三种响应形态

| 形态 | 涉及工具 | 示例 |
|---|---|---|
| 纯 JSON 信封 | `query-my-account`、`query-my-prizes`、`order-list`、`mall-points-products` | `{"success":true,"code":200,"data":{...}}` |
| **自然语言报告壳 + 内嵌 JSON** | `query-lottery-info`、`mall-points-products`（部分版本） | 先输出 `# API Response Information` + 字段字典，再在 `## Original Response` 下给 JSON |
| 纯 Markdown | `query-my-coupons`、`campaign-calendar`、`available-coupons` | `# 您的优惠券列表\n\n暂无可用优惠券` |

第二种最容易坑人：字段字典里的 `data[].spuName` 含方括号，若按"找第一个 `[` 或 `{` 再向后截取"的常规策略解析，**入口位置就是错的**，会静默返回 `None`。本项目实测时正是如此——`query-lottery-info` 与 `mall-points-products` 两个工具在首轮巡检中双双解析失败，而其他工具正常，极具迷惑性。

**处理**：`extract_json()` 不再猜单一偏移量，而是用 `json.JSONDecoder.raw_decode()` 扫描所有合法对象起点，取**能解码的最长跨度**。同时优先使用 `structuredContent`，仅在缺失时回退解析文本（`sentry/collect.py` 的 `_payload()`）。

### 5.4 另外两处真实数据陷阱

- **商城里存在过期商品**：部分 `status = 2`（上架）的商品 `downTime` 已经是历史时间（如 `2026-03-21`）。不过滤会虚高汇率——本项目实测中，`18.8元板烧鸡腿堡两件套 / 100 积分 = ¥0.188/分` 这个"最优汇率"其实来自一件已下架商品。剔除后最优汇率回落到 `¥0.138/分`。
- **`order-list` 空态返回 `data: {}`**，schema 里写的 `data.list` 并不存在。直接取 `data.list` 会抛 `KeyError`。

---

## 6. 写操作的安全边界

哨兵是全项目唯一会写账户的地方，规则如下：

1. **默认只读**。`patrol` 不加参数时，只调用 8 个只读工具。
2. **写操作需显式开关**。必须传 `--execute-claims` 才会调用 `auto-bind-coupons`。
3. **只自动执行幂等、零成本、无交易的动作**。领券不花钱、不下单、不可撤销地损失任何东西；一旦涉及 `create-order` / `draw-lottery` / `mall-create-order`，一律降级为"报告中提出建议，由用户在对话中确认"。
4. **写入前二次校验**。`_execute_claims()` 会先确认本次快照中 `claimable` 非空，避免空跑调用。
5. **演示模式禁止写入**。`--demo` 下 `snapshot.source != "live"`，直接跳过真实领券。

```python
# mcsentry.py
if args.execute_claims:
    return _execute_claims(snapshot)   # 只有显式传参才进入
```

---

## 7. 稳定性与成本

| 指标 | 实测/设计值 |
|---|---|
| 单次巡检请求数 | 9（含 `initialize` 与 `notifications/initialized` 共 11 次 HTTP） |
| 单轮耗时 | 约 3–6 秒（`mall-points-products` 响应体约 15 KB，占大头） |
| 限流余量 | 600 次/分钟，单次巡检仅用 9 次，留足 66 倍余量 |
| 失败隔离 | 单个工具失败不影响其余资产统计，失败项在报告「数据可信度」表中原样标注 |
| Token 处理 | 仅从环境变量 / `.env` 读取；日志中经 `mask()` 脱敏为 `1jCP****sluY`；**不写入任何报告产物** |

---

## 8. 复现方式

```bash
# 1. 检查本机到 MCP 的连通性（逐工具打表）
python mcsentry.py probe

# 2. 一次只读巡检，输出完整报告
python mcsentry.py patrol --full

# 3. 落盘原始快照，便于核对每个数字的来源
python mcsentry.py patrol --json out/snapshot.json
```

`probe` 的输出即为本文第 2 节表格的生成方式，任何人都可复跑验证。
