"""Deterministic valuation and prioritisation.

The rule for this module: **no number is allowed to be invented.** Money only
appears when it can be derived from two real observations —

* a point balance (``query-my-account``) times a yuan-per-point rate measured
  from the live redemption catalogue (``mall-points-products``), or
* a face value written into a coupon's own name (``下单立减3元券`` → 3.0).

When neither is available the asset is still surfaced as an action, but with
``value_cny = None`` and no currency in the headline. That distinction is what
keeps the report honest.

Point-rate derivation
---------------------
For every catalogue row with ``point > 0`` and ``price > 0`` that is currently
redeemable, ``rate = price / point``. Measured on 2026-10-18 this ranged from
¥0.027/point (21.9元巨无霸可乐组合, 800 分) to ¥0.188/point (18.8元板烧鸡腿堡
两件套, 100 分) — a 7x spread between the worst and the best way to spend the
same balance. The sentry reports the spread because "your points are worth less
than you think" is the actionable insight, not the average.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from statistics import median
from typing import Any

from .collect import Snapshot

FACE_VALUE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*元")
DISCOUNT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*折")


@dataclass
class Rate:
    best: float = 0.0
    worst: float = 0.0
    typical: float = 0.0
    best_item: str = ""
    worst_item: str = ""
    samples: int = 0

    @property
    def usable(self) -> bool:
        return self.samples > 0 and self.best > 0

    def describe(self) -> str:
        if not self.usable:
            return "未取到可兑换商品，积分暂不折算金额"
        return (
            f"最优 ¥{self.best:.3f}/分（{self.best_item}）"
            f" · 常规 ¥{self.typical:.3f}/分"
            f" · 最差 ¥{self.worst:.3f}/分（{self.worst_item}）"
            f" · 样本 {self.samples} 件"
        )


@dataclass
class Action:
    kind: str  # claim | points | coupon | draw | prize
    title: str
    detail: str
    value_cny: float | None = None
    deadline: datetime | None = None
    hours_left: float | None = None
    urgency: float = 0.0
    autonomous: bool = False  # can the sentry do it without asking
    tool: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "title": self.title,
            "detail": self.detail,
            "value_cny": None if self.value_cny is None else round(self.value_cny, 2),
            "deadline": self.deadline.strftime("%Y-%m-%d %H:%M") if self.deadline else None,
            "hours_left": None if self.hours_left is None else round(self.hours_left, 1),
            "urgency": round(self.urgency, 2),
            "autonomous": self.autonomous,
            "tool": self.tool,
        }


@dataclass
class Assessment:
    now: datetime
    rate: Rate
    at_risk_cny: float = 0.0
    already_lost_cny: float | None = None
    actions: list[Action] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    burn_ratio: float | None = None  # already-expired / accumulated

    @property
    def pending(self) -> list[Action]:
        return [a for a in self.actions if a.kind != "claim" or a.hours_left is not None]


def derive_rate(snapshot: Snapshot) -> Rate:
    rows = [p for p in snapshot.catalogue if p.redeemable(snapshot.taken_at)]
    if not rows:
        return Rate()
    best = max(rows, key=lambda p: p.rate)
    worst = min(rows, key=lambda p: p.rate)
    return Rate(
        best=best.rate,
        worst=worst.rate,
        typical=median(p.rate for p in rows),
        best_item=best.name,
        worst_item=worst.name,
        samples=len(rows),
    )


def month_end(moment: datetime, months_ahead: int = 0) -> datetime:
    year, month = moment.year, moment.month + months_ahead
    year += (month - 1) // 12
    month = (month - 1) % 12 + 1
    last_day = calendar.monthrange(year, month)[1]
    return datetime(year, month, last_day, 23, 59, 59)


def face_value(name: str) -> float | None:
    """Read a coupon's face value out of its own title, if stated."""
    text = name or ""
    m = FACE_VALUE_RE.search(text)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


def _hours_until(deadline: datetime | None, now: datetime) -> float | None:
    if deadline is None:
        return None
    return max((deadline - now).total_seconds() / 3600.0, 0.0)


def assess(snapshot: Snapshot) -> Assessment:
    now = snapshot.taken_at
    rate = derive_rate(snapshot)
    result = Assessment(now=now, rate=rate)

    # ---------------------------------------------------------- point buckets
    if rate.usable:
        buckets = (
            ("本月将过期", snapshot.expiring_this_month, month_end(now, 0)),
            ("下月将过期", snapshot.expiring_next_month, month_end(now, 1)),
        )
    else:
        buckets = (
            ("本月将过期", snapshot.expiring_this_month, month_end(now, 0)),
            ("下月将过期", snapshot.expiring_next_month, month_end(now, 1)),
        )

    for label, points, deadline in buckets:
        if points <= 0:
            continue
        hours = _hours_until(deadline, now)
        value = points * rate.best if rate.usable else None
        detail = f"{points:g} 积分将在 {deadline:%m-%d} 到期清零"
        if value is not None:
            detail += f"，按最优兑换率折合约 ¥{value:.2f}"
        result.actions.append(
            Action(
                kind="points",
                title=f"{label}：{points:g} 积分",
                detail=detail,
                value_cny=value,
                deadline=deadline,
                hours_left=hours,
                urgency=(value or points / 100) / max(hours or 1, 1),
                autonomous=False,
                tool="mall-create-order",
            )
        )
        if value:
            result.at_risk_cny += value

    # --------------------------------------------------------------- coupons
    for coupon in snapshot.coupons:
        hours = _hours_until(coupon.expiry, now)
        value = face_value(coupon.name)
        detail = "持有券"
        if coupon.expiry:
            detail = f"有效期至 {coupon.expiry:%Y-%m-%d %H:%M}"
        elif face_value(coupon.name) is None:
            detail = "持有券（未解析到有效期）"
        result.actions.append(
            Action(
                kind="coupon",
                title=coupon.name,
                detail=detail,
                value_cny=value,
                deadline=coupon.expiry,
                hours_left=hours,
                urgency=(value or 1.0) / max(hours or 240, 1),
                autonomous=False,
                tool="calculate-price",
            )
        )
        if value and hours is not None:
            result.at_risk_cny += value

    # ------------------------------------------------------------- claimable
    if snapshot.claimable:
        total = sum(v for v in (face_value(n) for n in snapshot.claimable) if v)
        names = "、".join(snapshot.claimable[:3])
        more = f" 等 {len(snapshot.claimable)} 张" if len(snapshot.claimable) > 3 else ""
        result.actions.append(
            Action(
                kind="claim",
                title=f"可领券 {len(snapshot.claimable)} 张",
                detail=f"{names}{more}，领取零成本、零风险",
                value_cny=total or None,
                hours_left=None,
                urgency=100.0 if total else 60.0,
                autonomous=True,
                tool="auto-bind-coupons",
            )
        )
        if total:
            result.at_risk_cny += total

    # ---------------------------------------------------------------- lottery
    lottery = snapshot.lottery or {}
    decision = lottery.get("drawDecision") or {}
    times = lottery.get("availableTimes")
    if isinstance(times, int) and times > 0 and decision.get("resourceEligible", True):
        result.actions.append(
            Action(
                kind="draw",
                title=f"可用抽奖次数 {times} 次",
                detail=f"活动「{lottery.get('activityName', '积分抽奖')}」"
                f"剩余 {times} 次，奖品含 "
                + "、".join(
                    str(p.get("name")) for p in (lottery.get("prizes") or [])[:3] if p.get("name")
                ),
                hours_left=None,
                urgency=40.0,
                autonomous=False,
                tool="draw-lottery",
            )
        )
    elif decision.get("resourceEligible") is False:
        need = (decision.get("nextConsumption") or {}).get("points")
        if need:
            result.notes.append(
                f"积分抽奖单次需 {need} 积分，当前可用 "
                f"{snapshot.available_points:g} 分，暂不满足条件"
                f"（{decision.get('reason') or '资源不足'}）"
            )

    # ----------------------------------------------------------------- prizes
    for prize in snapshot.prizes:
        status_text = str(prize.get("statusText") or "")
        remind = str(prize.get("timeRemindText") or "")
        if any(word in status_text for word in ("未领取", "待领取", "未使用")):
            result.actions.append(
                Action(
                    kind="prize",
                    title=str(prize.get("name") or "奖品待领取"),
                    detail=f"{status_text} {remind}".strip(),
                    hours_left=None,
                    urgency=45.0,
                    autonomous=False,
                    tool="query-my-prizes",
                )
            )

    # --------------------------------------------------------- already burned
    expired = snapshot.already_expired
    if expired > 0:
        if rate.usable:
            result.already_lost_cny = expired * rate.best
        accumulated = snapshot.accumulative
        if accumulated > 0:
            result.burn_ratio = expired / accumulated

    # ------------------------------------------------------------ provenance
    if not rate.usable:
        result.notes.append(
            "未取得积分兑换汇率（mall-points-products 无可用样本），"
            "本报告不对积分做金额折算，仅按分值排序。"
        )
    failed = snapshot.failed_tools()
    if failed:
        result.notes.append(
            "以下工具本次未成功返回，相关资产未纳入统计：" + "、".join(failed)
        )
    if snapshot.probes and any(p.tool == "query-my-coupons" and p.ok for p in snapshot.probes):
        coupon_probe = next(p for p in snapshot.probes if p.tool == "query-my-coupons")
        if coupon_probe.empty:
            result.notes.append("券列表为空（query-my-coupons 明确返回「暂无可用优惠券」）。")

    # Ranking rule: anything with a hard deadline that destroys value ranks
    # above anything that merely adds value. A coupon expiring tomorrow is more
    # urgent than a coupon you could claim at any time, regardless of face
    # value. Within the time-bound group, soonest deadline first; ties break on
    # money at stake.
    def _rank(action: Action) -> tuple:
        time_bound = action.hours_left is not None
        return (
            0 if time_bound else 1,
            action.hours_left if time_bound else 0.0,
            -(action.value_cny or 0.0),
        )

    result.actions.sort(key=_rank)
    return result


def humanize_hours(hours: float | None) -> str:
    """Readable duration for a push notification."""
    if hours is None:
        return "无固定截止"
    if hours < 1:
        return "不到 1 小时"
    if hours < 48:
        return f"{hours:.0f} 小时"
    return f"{hours / 24:.0f} 天"
