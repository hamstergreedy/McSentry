"""Report rendering.

Two surfaces, one source of truth:

* :func:`render_markdown` — the long form. Structured, auditable, with a
  provenance table so a reader can see which tool backed each claim.
* :func:`render_push` — what actually lands in a mailbox or a chat window.
  Short enough to be read on a lock screen, which is the whole point of an
  unattended patrol.
"""

from __future__ import annotations

from typing import Iterable

from . import __version__
from .collect import Snapshot
from .valuate import Action, Assessment, humanize_hours


def _money(value: float | None) -> str:
    return "—" if value is None else f"¥{value:,.2f}"


def _deadline(action: Action) -> str:
    if action.deadline:
        return f"{action.deadline:%m-%d %H:%M}"
    if action.hours_left is not None:
        return f"剩 {humanize_hours(action.hours_left)}"
    return "无固定截止"


def headline(snapshot: Snapshot, result: Assessment) -> str:
    """One sentence a human can act on, built only from measured facts.

    The sentence leads with the *total* value that will be destroyed, then names
    the single largest item. Leading with the nearest deadline instead would
    report a ¥3 coupon expiring tomorrow while ¥88 of points quietly burns two
    weeks later — technically true, practically useless.
    """
    timed = [a for a in result.actions if a.hours_left is not None]
    valued = [a for a in timed if a.value_cny]
    if valued:
        total = sum(a.value_cny or 0.0 for a in valued)
        biggest = max(valued, key=lambda a: a.value_cny or 0.0)
        count = len(valued)
        last = max((a for a in timed if a.deadline), key=lambda a: a.deadline, default=None)
        window = f"到 {last.deadline:%m-%d} 为止" if last else "近期"
        return (
            f"{window}，你有 {count} 项资产共约 {_money(total)} 会清零；"
            f"最大一笔是「{biggest.title}」，{humanize_hours(biggest.hours_left)}后到期。"
        )
    if timed:
        nearest = min(timed, key=lambda a: a.hours_left or 0)
        return f"有 {len(timed)} 项资产将在 {humanize_hours(nearest.hours_left)}内失效（无金额可折算）。"
    claim = next((a for a in result.actions if a.kind == "claim"), None)
    if claim:
        return f"有 {claim.title} 未领，领取零成本，建议现在就领掉。"
    if result.already_lost_cny:
        return (
            f"当前没有即将过期的资产，但历史已蒸发 {snapshot.already_expired:g} 积分，"
            f"折合约 {_money(result.already_lost_cny)}——这个账户一直没有人在盯。"
        )
    return "本次巡检未发现即将过期的资产，账户状态健康。"


def render_markdown(snapshot: Snapshot, result: Assessment) -> str:
    lines: list[str] = []
    add = lines.append

    add("# 麦麦账户哨兵 · 巡检报告")
    add("")
    add(
        f"> 巡检时间 **{snapshot.taken_at:%Y-%m-%d %H:%M:%S}**（MCP 服务器时间）　"
        f"数据源 **{snapshot.source}**　版本 v{__version__}"
    )
    add("")
    add("## 一句话结论")
    add("")
    add(f"**{headline(snapshot, result)}**")
    add("")

    # ---------------------------------------------------------------- at risk
    add("## 一、待办清单（按紧迫度排序）")
    add("")
    if not result.actions:
        add("本次巡检没有需要处理的事项。")
    else:
        add("| # | 类型 | 事项 | 说明 | 折合价值 | 截止 | 执行方式 |")
        add("|---:|---|---|---|---:|---|---|")
        for idx, action in enumerate(result.actions, 1):
            auto = "哨兵自动完成" if action.autonomous else "需你确认"
            kind = "⏳ 会清零" if action.hours_left is not None else "✅ 零成本"
            add(
                f"| {idx} | {kind} | {action.title} | {action.detail} | "
                f"{_money(action.value_cny)} | {_deadline(action)} | {auto} |"
            )
    add("")

    # ------------------------------------------------------------ burn gauge
    add("## 二、已经流走的部分")
    add("")
    add("| 指标 | 数值 |")
    add("|---|---:|")
    add(f"| 累计积分 | {snapshot.accumulative:g} |")
    add(f"| 账户可用积分 | {snapshot.available_points:g} |")
    add(f"| 已过期积分 | {snapshot.already_expired:g} |")
    add(f"| 已使用积分 | {snapshot.account.get('usedPoint', '—')} |")
    if result.burn_ratio is not None:
        add(f"| 过期占累计比例 | {result.burn_ratio * 100:.1f}% |")
    if result.already_lost_cny:
        add(f"| 已蒸发价值（按最优兑换率估算） | {_money(result.already_lost_cny)} |")
    add("")

    # ------------------------------------------------------------------ rate
    add("## 三、积分实际购买力（实测）")
    add("")
    if result.rate.usable:
        add(
            f"基于 {result.rate.samples} 件**当前可兑换**商品计算，"
            f"1 积分的真实价值区间是 "
            f"**¥{result.rate.worst:.3f} ~ ¥{result.rate.best:.3f}**。"
        )
        add("")
        add("| 口径 | 元/积分 | 代表商品 |")
        add("|---|---:|---|")
        add(f"| 最优 | {result.rate.best:.3f} | {result.rate.best_item} |")
        add(f"| 常规（中位数） | {result.rate.typical:.3f} | — |")
        add(f"| 最差 | {result.rate.worst:.3f} | {result.rate.worst_item} |")
        add("")
        spread = result.rate.best / result.rate.worst if result.rate.worst else 0
        if spread:
            add(
                f"同样一笔积分，兑换方式不同，价值最多相差 **{spread:.1f} 倍**。"
                "这是账户里最容易被忽略的一项隐性损失。"
            )
    else:
        add("本次未取得可兑换商品样本，积分不折算金额。")
    add("")

    # --------------------------------------------------------------- notices
    add("## 四、说明与数据可信度")
    add("")
    for note in result.notes:
        add(f"- {note}")
    if not result.notes:
        add("- 全部只读工具均正常返回。")
    add("")
    add("| 工具 | 状态 | 备注 |")
    add("|---|---|---|")
    for probe in snapshot.probes:
        status = "✅ 成功" if probe.ok else "❌ 失败"
        if probe.ok and probe.empty:
            status = "⚪ 空结果"
        note = probe.error or probe.note or ""
        note = note.replace("|", "/")
        if len(note) > 90:
            note = note[:89] + "…"
        add(f"| `{probe.tool}` | {status} | {note} |")
    add("")

    if snapshot.unparsed:
        add("### 未结构化的原始返回（保留原文，未做猜测）")
        add("")
        for tool, text in snapshot.unparsed.items():
            snippet = " ".join(text.split())[:240]
            add(f"- `{tool}`：{snippet}")
        add("")

    add("---")
    add("")
    add(
        "本报告由麦账户哨兵自动生成，所有金额均由实测数据推算，不构成麦当劳官方信息；"
        "餐品与券的实际规则以麦当劳官方渠道为准。"
    )
    return "\n".join(lines)


def render_push(snapshot: Snapshot, result: Assessment, *, limit: int = 4) -> str:
    """Short form for email subject body / chat push."""

    def row(idx: int, action: Action) -> list[str]:
        tail = []
        if action.value_cny:
            tail.append(_money(action.value_cny))
        if action.deadline:
            tail.append(f"{action.deadline:%m-%d %H:%M} 前")
        suffix = f"（{'，'.join(tail)}）" if tail else ""
        out = [f"{idx}. {action.title}{suffix}"]
        if action.detail:
            out.append(f"   {action.detail}")
        return out

    lines = [f"🍟 麦麦账户哨兵 · {snapshot.taken_at:%m-%d %H:%M}"]
    lines.append("")
    lines.append(headline(snapshot, result))
    lines.append("")

    time_bound = [a for a in result.actions if a.hours_left is not None][:limit]
    quick = [a for a in result.actions if a.hours_left is None][:3]

    if time_bound:
        lines.append("⏳ 会被清零的：")
        for idx, action in enumerate(time_bound, 1):
            lines.extend(row(idx, action))
        lines.append("")
    if quick:
        lines.append("✅ 零成本可做的：")
        for idx, action in enumerate(quick, 1):
            lines.extend(row(idx, action))
        lines.append("")
    if not time_bound and not quick:
        lines.append("今天没有需要处理的资产。")
        lines.append("")

    if result.already_lost_cny:
        lines.append(
            f"⚠️ 已经蒸发的部分：{snapshot.already_expired:g} 积分"
            f"（占总累计 {(result.burn_ratio or 0) * 100:.0f}%），"
            f"折合约 {_money(result.already_lost_cny)}。"
        )
        lines.append("")

    if result.rate.usable:
        lines.append(
            f"💡 积分购买力实测：¥{result.rate.worst:.3f} ~ ¥{result.rate.best:.3f}/分，"
            f"最优换法是「{result.rate.best_item}」。"
        )
        lines.append("")

    failed = snapshot.failed_tools()
    if failed:
        lines.append(f"（本次未取到：{'、'.join(failed)}）")
        lines.append("")

    lines.append("—— 麦麦账户哨兵 · 无人值守巡检")
    return "\n".join(lines)


def render_table(rows: Iterable[tuple[str, str]]) -> str:
    return "\n".join(f"| {a} | {b} |" for a, b in rows)
