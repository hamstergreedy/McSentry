"""Response normalisation.

McDonald's MCP tools do **not** share one response shape. Observed on a live
account (2026-10-18, server v1.0.9):

===============  ========================================  ================
Tool             Shape                                     Notes
===============  ========================================  ================
query-my-account JSON envelope, ``data`` object            10 numeric fields as
                                                           *strings*
query-my-prizes  JSON envelope, ``data.prizes`` array      pagination fields
order-list       JSON envelope, ``data`` = ``{}``          **no** ``list`` key
                                                           when empty
mall-points-     JSON envelope, ``data`` array             ``point``/``price``
products                                                   as strings
query-my-coupons ``text/plain`` **markdown**, not JSON     header + paragraph
campaign-calendar ``text/plain`` **markdown**              long prose blob
available-coupons markdown + ``data: null`` when empty     see below
===============  ========================================  ================

The ``available-coupons`` case deserves a note. Its ``outputSchema`` declares
``data`` as an array, but the server sends ``data: null`` whenever the member
has no claimable coupons. Schema-validating MCP clients therefore raise
``-32602 Structured content does not match the tool's output schema`` *exactly
when the answer is "nothing to claim"*. Reading the raw ``content[].text``
avoids that false failure. Anything the sentry cannot parse is preserved
verbatim and surfaced as ``unparsed`` rather than silently dropped.
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterable

TEXT_EMPTY_MARKERS = (
    "暂无",
    "没有",
    "无可用",
    "no coupon",
    "nothing",
)


def extract_json(text: str) -> Any:
    """Best-effort JSON extraction from a tool's text payload.

    Several McDonald's tools answer with a *human-readable report* rather than
    raw JSON — a ``# API Response Information`` preamble, a field dictionary,
    then the payload under ``## Original Response``. Naive "find the first
    bracket" parsing breaks on that preamble, because field paths like
    ``data[].spuName`` contain a ``[`` long before the JSON starts. So instead
    of guessing one start offset we let the decoder scan every plausible object
    start and keep the longest span it can decode.
    """
    if not text:
        return None
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    fenced = re.search(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", text, re.S)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except json.JSONDecodeError:
            pass

    decoder = json.JSONDecoder()
    best: tuple[Any, int] | None = None
    for index, char in enumerate(text):
        if char not in "{[":
            continue
        # A JSON value starts with "{" + quote/}, or "[" + value/]. Anything
        # else (like ``data[]`` inside prose) is not worth an attempt.
        nxt = text[index + 1 : index + 2]
        if char == "{" and nxt not in ('"', "}"):
            continue
        if char == "[" and nxt not in ('"', "{", "[", "]", "-") and not nxt.isdigit():
            continue
        try:
            obj, end = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, (dict, list)):
            continue
        if best is None or end > best[1]:
            best = (obj, end)
    return best[0] if best else None


def dig(obj: Any, *path: str, default: Any = None) -> Any:
    """Safe nested lookup."""
    cur = obj
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur


def first_list(obj: Any, keys: Iterable[str]) -> list[Any]:
    """Return the first list found under any of ``keys``, breadth-first."""
    queue: list[Any] = [obj]
    wanted = tuple(keys)
    while queue:
        cur = queue.pop(0)
        if isinstance(cur, list):
            return cur
        if isinstance(cur, dict):
            for key in wanted:
                val = cur.get(key)
                if isinstance(val, list):
                    return val
            queue.extend(v for v in cur.values() if isinstance(v, (dict, list)))
    return []


def looks_empty(text: str) -> bool:
    if not text.strip():
        return True
    lowered = text.lower()
    return any(marker in lowered for marker in TEXT_EMPTY_MARKERS)


def parse_markdown_table(text: str) -> list[dict[str, str]]:
    """Parse a GitHub-flavoured markdown table into rows of strings.

    ``query-my-coupons`` renders as prose/table rather than JSON. We cannot see
    a populated payload on a coupon-less account, so this parser is written
    against the general table shape and degrades to ``[]`` when there is no
    table — the caller then keeps the raw text.
    """
    lines = [ln.strip() for ln in text.splitlines() if ln.strip().startswith("|")]
    if len(lines) < 3:
        return []
    def cells(line: str) -> list[str]:
        return [c.strip() for c in line.strip("|").split("|")]

    header = cells(lines[0])
    if all(re.fullmatch(r":?-{2,}:?", c) for c in cells(lines[1])):
        body = lines[2:]
    else:
        body = lines[1:]
    rows: list[dict[str, str]] = []
    for line in body:
        values = cells(line)
        if len(values) != len(header):
            continue
        rows.append(dict(zip(header, values)))
    return rows


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


def strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "").strip()


def compact(text: str, limit: int = 160) -> str:
    text = re.sub(r"\s+", " ", strip_html(text)).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"
