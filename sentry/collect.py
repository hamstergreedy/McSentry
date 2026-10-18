"""Asset collection: turn MCP tool responses into a typed snapshot.

Every field keeps its provenance (which tool produced it, whether the call
succeeded, whether the answer was empty). The report is only allowed to state a
fact if the matching probe succeeded — this is what stops the sentry from
inventing a coupon that does not exist.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from . import normalize as nz
from .mcp_client import McpClient, ToolResult

DATE_PATTERNS = (
    (r"(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})", "ymdhms"),
    (r"(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})", "ymdhm"),
    (r"(\d{4})-(\d{2})-(\d{2})", "ymd"),
    (r"(\d{4})/(\d{2})/(\d{2})", "ymd"),
    (r"(\d{4})年(\d{1,2})月(\d{1,2})日\s*(\d{1,2}):(\d{2})", "ymdhm"),
    (r"(\d{4})年(\d{1,2})月(\d{1,2})日", "ymd"),
    (r"(\d{1,2})月(\d{1,2})日\s*(\d{1,2}):(\d{2})", "mdhm"),
    (r"(\d{1,2})月(\d{1,2})日", "md"),
)


def parse_expiry(value: Any, now: datetime) -> datetime | None:
    """Parse the many date shapes that coupon payloads may use.

    Returns ``None`` when no deadline can be read, which the valuation layer
    treats as "unknown", never as "expires soon".
    """
    text = str(value or "").strip()
    if not text:
        return None
    for pattern, kind in DATE_PATTERNS:
        m = re.search(pattern, text)
        if not m:
            continue
        try:
            if kind == "ymdhms":
                return datetime(*map(int, m.groups()[:6]))
            if kind == "ymdhm":
                return datetime(*map(int, m.groups()[:5]))
            if kind == "ymd":
                return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), 23, 59, 59)
            if kind == "mdhm":
                month, day, hour, minute = map(int, m.groups()[:4])
                year = now.year + (1 if month < now.month else 0)
                return datetime(year, month, day, hour, minute)
            if kind == "md":
                month, day = map(int, m.groups()[:2])
                year = now.year + (1 if month < now.month else 0)
                return datetime(year, month, day, 23, 59, 59)
        except ValueError:
            continue
    return None


@dataclass
class Probe:
    tool: str
    ok: bool
    empty: bool = False
    error: str | None = None
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "ok": self.ok,
            "empty": self.empty,
            "error": self.error,
            "note": self.note,
        }


@dataclass
class Product:
    name: str
    point: float
    price: float
    up_time: datetime | None
    down_time: datetime | None
    status: int
    category: str

    @property
    def rate(self) -> float:
        """Yuan of retail value per point."""
        return self.price / self.point if self.point > 0 else 0.0

    def redeemable(self, now: datetime) -> bool:
        if self.point <= 0 or self.price <= 0 or self.status != 2:
            return False
        if self.up_time and now < self.up_time:
            return False
        if self.down_time and now > self.down_time:
            return False
        return True


@dataclass
class Coupon:
    name: str
    expiry: datetime | None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class Snapshot:
    taken_at: datetime
    account: dict[str, Any] = field(default_factory=dict)
    coupons: list[Coupon] = field(default_factory=list)
    claimable: list[str] = field(default_factory=list)
    lottery: dict[str, Any] = field(default_factory=dict)
    prizes: list[dict[str, Any]] = field(default_factory=list)
    orders: list[dict[str, Any]] = field(default_factory=list)
    catalogue: list[Product] = field(default_factory=list)
    campaign_text: str = ""
    probes: list[Probe] = field(default_factory=list)
    unparsed: dict[str, str] = field(default_factory=dict)
    source: str = "live"

    # --- point helpers -----------------------------------------------------

    @property
    def available_points(self) -> float:
        return nz.as_float(self.account.get("availablePoint"))

    @property
    def expiring_this_month(self) -> float:
        return nz.as_float(self.account.get("currentMouthExpirePoint"))

    @property
    def expiring_next_month(self) -> float:
        return nz.as_float(self.account.get("nextMouthExpirePoint"))

    @property
    def already_expired(self) -> float:
        return nz.as_float(self.account.get("expiredPoint"))

    @property
    def accumulative(self) -> float:
        return nz.as_float(self.account.get("accumulativePoint"))

    def probe(self, tool: str) -> Probe | None:
        return next((p for p in self.probes if p.tool == tool), None)

    def failed_tools(self) -> list[str]:
        return [p.tool for p in self.probes if not p.ok]

    def to_json(self) -> dict[str, Any]:
        def iso(dt: datetime | None) -> str | None:
            return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else None

        # accountId identifies the member. Reports get shared, screenshotted and
        # committed, so it is masked on the way out — the report never needs the
        # real id to be useful.
        account = dict(self.account)
        if account.get("accountId"):
            raw = str(account["accountId"])
            account["accountId"] = f"{raw[:4]}****{raw[-4:]}" if len(raw) > 8 else "****"

        return {
            "taken_at": iso(self.taken_at),
            "source": self.source,
            "account": account,
            "coupons": [{"name": c.name, "expiry": iso(c.expiry)} for c in self.coupons],
            "claimable": self.claimable,
            "lottery": self.lottery,
            "prizes": self.prizes,
            "orders": self.orders,
            "catalogue": [
                {
                    "name": p.name,
                    "point": p.point,
                    "price": p.price,
                    "category": p.category,
                    "status": p.status,
                    "up_time": iso(p.up_time),
                    "down_time": iso(p.down_time),
                }
                for p in self.catalogue
            ],
            "probes": [p.to_dict() for p in self.probes],
            "unparsed": self.unparsed,
        }


# --------------------------------------------------------------------- build


MONEY_IN_NAME = re.compile(r"(\d+(?:\.\d+)?)\s*元")


def _face_value(name: str) -> float:
    """Face value written into a product/coupon title.

    ``mall-points-products`` returns ``price: "0"`` for every points-redeemable
    voucher — the actual yuan amount only appears in ``spuName``
    (``"10.9元指定小食任选1份"``). Without this fallback the whole points
    catalogue looks priceless and the exchange rate cannot be derived.
    """
    m = MONEY_IN_NAME.search(name or "")
    if not m:
        return 0.0
    try:
        return float(m.group(1))
    except ValueError:
        return 0.0


def _product(raw: dict[str, Any]) -> Product | None:
    name = str(raw.get("spuName") or "").strip()
    if not name:
        return None
    point = nz.as_float(raw.get("point"))
    price = nz.as_float(raw.get("price"))
    if price <= 0:
        price = _face_value(name)
    return Product(
        name=name,
        point=point,
        price=price,
        up_time=parse_expiry(raw.get("upTime"), datetime.now()) if raw.get("upTime") else None,
        down_time=parse_expiry(raw.get("downTime"), datetime.now())
        if raw.get("downTime")
        else None,
        status=nz.as_int(raw.get("status"), 2),
        category=str(raw.get("catName") or ""),
    )


def _payload(result: ToolResult) -> Any:
    """Prefer the structured payload, fall back to parsing the text report.

    The server ships most tools twice: a ``structuredContent`` envelope and a
    prose "API Response Information" report. Which one is usable varies per
    tool, so try the structured form first and only parse prose when it is
    missing.
    """
    structured = result.structured
    if isinstance(structured, (dict, list)) and structured:
        return structured
    return nz.extract_json(result.text)


def _coupons_from(result: ToolResult, now: datetime) -> tuple[list[Coupon], str]:
    """Coupons live in markdown on this API. Parse a table if present."""
    payload = _payload(result)
    rows: list[dict[str, Any]] = []
    if isinstance(payload, dict):
        rows = nz.first_list(payload, ("coupons", "list", "couponList", "data"))
    elif isinstance(payload, list):
        rows = payload
    if not rows:
        rows = nz.parse_markdown_table(result.text)

    coupons: list[Coupon] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(
            row.get("券名称") or row.get("couponName") or row.get("name") or row.get("标题") or ""
        ).strip()
        if not name:
            continue
        expiry = None
        for key in ("有效期", "有效期至", "过期时间", "expireTime", "expiryTime", "endTime", "deadline"):
            if row.get(key):
                expiry = parse_expiry(row[key], now)
                if expiry:
                    break
        coupons.append(Coupon(name=name, expiry=expiry, raw=row))
    return coupons, "" if coupons else result.text


def _claimable_from(result: ToolResult) -> list[str]:
    payload = _payload(result)
    rows: list[Any] = []
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            rows = data
        else:
            rows = nz.first_list(payload, ("coupons", "list"))
    elif isinstance(payload, list):
        rows = payload
    names: list[str] = []
    for row in rows:
        if isinstance(row, dict):
            name = row.get("couponName") or row.get("name") or row.get("title")
            if name:
                names.append(str(name).strip())
        elif isinstance(row, str):
            names.append(row.strip())
    return names


def collect(client: McpClient, now: datetime | None = None, *, include_catalogue: bool = True) -> Snapshot:
    """Run the read-only probe set and build a :class:`Snapshot`."""

    probes: list[Probe] = []
    unparsed: dict[str, str] = {}

    # Authoritative time first: the report's deadline math must not depend on
    # the local clock drifting.
    time_result = client.call("now-time-info")
    server_now = None
    if time_result.ok:
        payload = _payload(time_result)
        stamp = nz.dig(payload, "data", "datetime") if isinstance(payload, dict) else None
        server_now = parse_expiry(stamp, now or datetime.now()) if stamp else None
    probes.append(
        Probe(
            "now-time-info",
            ok=time_result.ok,
            error=time_result.error,
            note=f"server time {server_now}" if server_now else "local clock used",
        )
    )
    now = server_now or now or datetime.now()

    account_result = client.call("query-my-account")
    account: dict[str, Any] = {}
    if account_result.ok:
        payload = _payload(account_result)
        account = nz.dig(payload, "data", default={}) if isinstance(payload, dict) else {}
        if not isinstance(account, dict):
            account = {}
    if not account:
        unparsed["query-my-account"] = account_result.text
    probes.append(
        Probe("query-my-account", ok=account_result.ok and bool(account), error=account_result.error)
    )

    coupons_result = client.call("query-my-coupons")
    coupons, leftover = _coupons_from(coupons_result, now)
    if leftover.strip():
        unparsed["query-my-coupons"] = coupons_result.text
    probes.append(
        Probe(
            "query-my-coupons",
            ok=coupons_result.ok,
            empty=nz.looks_empty(coupons_result.text) or not coupons,
            error=coupons_result.error,
            note="markdown payload" if coupons_result.ok else "",
        )
    )

    claim_result = client.call("available-coupons")
    claimable = _claimable_from(claim_result) if claim_result.ok else []
    probe_note = ""
    if not claim_result.ok and claim_result.error and "-32602" in claim_result.error:
        probe_note = "outputSchema/missing-array mismatch on empty result (known)"
    probes.append(
        Probe(
            "available-coupons",
            ok=claim_result.ok,
            empty=not claimable,
            error=claim_result.error,
            note=probe_note,
        )
    )

    lottery_result = client.call("query-lottery-info")
    lottery: dict[str, Any] = {}
    if lottery_result.ok:
        payload = _payload(lottery_result)
        lottery = nz.dig(payload, "data", default={}) if isinstance(payload, dict) else {}
        if not isinstance(lottery, dict):
            lottery = {}
    probes.append(Probe("query-lottery-info", ok=lottery_result.ok and bool(lottery)))

    prizes_result = client.call("query-my-prizes")
    prizes: list[dict[str, Any]] = []
    if prizes_result.ok:
        payload = _payload(prizes_result)
        prizes = nz.first_list(payload, ("prizes",)) if isinstance(payload, dict) else (
            payload if isinstance(payload, list) else []
        )
    probes.append(
        Probe("query-my-prizes", ok=prizes_result.ok, empty=not prizes, error=prizes_result.error)
    )

    orders_result = client.call("order-list")
    orders: list[dict[str, Any]] = []
    if orders_result.ok:
        payload = _payload(orders_result)
        if isinstance(payload, dict):
            orders = nz.first_list(payload, ("list", "orders", "orderList"))
    probes.append(
        Probe("order-list", ok=orders_result.ok, empty=not orders, error=orders_result.error)
    )

    catalogue: list[Product] = []
    if include_catalogue:
        mall_result = client.call("mall-points-products")
        if mall_result.ok:
            payload = _payload(mall_result)
            rows = (
                nz.dig(payload, "data", default=[])
                if isinstance(payload, dict)
                else payload or []
            )
            if isinstance(rows, list):
                catalogue = [p for p in (_product(r) for r in rows if isinstance(r, dict)) if p]
        probes.append(
            Probe(
                "mall-points-products",
                ok=mall_result.ok and bool(catalogue),
                empty=not catalogue,
                error=mall_result.error,
            )
        )

    campaign_result = client.call("campaign-calendar")
    probes.append(
        Probe(
            "campaign-calendar",
            ok=campaign_result.ok,
            empty=nz.looks_empty(campaign_result.text),
            error=campaign_result.error,
            note="markdown payload",
        )
    )

    return Snapshot(
        taken_at=now,
        account=account,
        coupons=coupons,
        claimable=claimable,
        lottery=lottery,
        prizes=[p for p in prizes if isinstance(p, dict)],
        orders=[o for o in orders if isinstance(o, dict)],
        catalogue=catalogue,
        campaign_text=campaign_result.text if campaign_result.ok else "",
        probes=probes,
        unparsed=unparsed,
        source="live",
    )


def load_snapshot(path: str | Path) -> Snapshot:
    """Load a snapshot from JSON (used by ``--demo`` and by cached runs)."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    now = parse_expiry(raw.get("taken_at"), datetime.now()) or datetime.now()
    return Snapshot(
        taken_at=now,
        account=raw.get("account") or {},
        coupons=[
            Coupon(name=c.get("name", ""), expiry=parse_expiry(c.get("expiry"), now))
            for c in raw.get("coupons") or []
        ],
        claimable=list(raw.get("claimable") or []),
        lottery=raw.get("lottery") or {},
        prizes=list(raw.get("prizes") or []),
        orders=list(raw.get("orders") or []),
        catalogue=[
            Product(
                name=p.get("name", ""),
                point=nz.as_float(p.get("point")),
                price=nz.as_float(p.get("price")),
                up_time=parse_expiry(p.get("up_time"), now) if p.get("up_time") else None,
                down_time=parse_expiry(p.get("down_time"), now) if p.get("down_time") else None,
                status=nz.as_int(p.get("status"), 2),
                category=p.get("category", ""),
            )
            for p in raw.get("catalogue") or []
        ],
        probes=[
            Probe(
                tool=p.get("tool", "?"),
                ok=bool(p.get("ok")),
                empty=bool(p.get("empty")),
                error=p.get("error"),
                note=p.get("note", ""),
            )
            for p in raw.get("probes") or []
        ],
        unparsed=raw.get("unparsed") or {},
        source="fixture",
    )
