#!/usr/bin/env python3
"""McAccount Sentinel CLI.

    python mcsentry.py patrol              # live read-only patrol, print report
    python mcsentry.py patrol --demo       # run against fixtures, no token needed
    python mcsentry.py patrol --push email # patrol then deliver
    python mcsentry.py probe               # show which MCP tools answered

Design rule: ``patrol`` never mutates the account unless ``--execute-claims`` is
passed explicitly. The default is read-only, and every write has to be asked
for twice (flag + confirmation is documented in the README).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

from sentry import __version__
from sentry.collect import collect, load_snapshot
from sentry.config import load_config, mask
from sentry.mcp_client import McpClient, McpError
from sentry.pushers import DeliveryError, get_channel
from sentry.pushers.base import Message
from sentry.report import headline, render_markdown, render_push
from sentry.valuate import assess

PROJECT_ROOT = Path(__file__).resolve().parent
DEMO_FIXTURE = PROJECT_ROOT / "fixtures" / "demo_snapshot.json"


def _build_snapshot(args) -> "object":
    if args.demo:
        if not DEMO_FIXTURE.is_file():
            raise SystemExit(f"演示数据缺失: {DEMO_FIXTURE}")
        snapshot = load_snapshot(DEMO_FIXTURE)
        # Re-anchor the fixture on today while keeping each deadline's *day
        # offset*, so a demo run always shows a plausible countdown no matter
        # when it is executed. Times snap to 23:59:59 to read like real
        # end-of-day expiry.
        original = snapshot.taken_at
        today = datetime.now()
        snapshot.taken_at = today
        for coupon in snapshot.coupons:
            if coupon.expiry:
                offset = (coupon.expiry.date() - original.date()).days
                day = today.date() + timedelta(days=offset)
                coupon.expiry = datetime(day.year, day.month, day.day, 23, 59, 59)
        return snapshot

    config = load_config(args.token, args.endpoint)
    print(f"· 连接 {config.endpoint}（token {mask(config.token)}）", file=sys.stderr)
    with McpClient(config.token, config.endpoint, timeout=config.timeout) as client:
        return collect(client, include_catalogue=not args.no_catalogue)


def cmd_patrol(args) -> int:
    snapshot = _build_snapshot(args)
    result = assess(snapshot)

    markdown = render_markdown(snapshot, result)
    push_text = render_push(snapshot, result)
    subject = f"麦麦账户哨兵 · {snapshot.taken_at:%m-%d} · {headline(snapshot, result)[:40]}"

    if args.json:
        payload = snapshot.to_json()
        payload["assessment"] = {
            "headline": headline(snapshot, result),
            "at_risk_cny": round(result.at_risk_cny, 2),
            "already_lost_cny": None
            if result.already_lost_cny is None
            else round(result.already_lost_cny, 2),
            "burn_ratio": result.burn_ratio,
            "rate": {
                "best": result.rate.best,
                "typical": result.rate.typical,
                "worst": result.rate.worst,
                "samples": result.rate.samples,
            },
            "actions": [a.to_dict() for a in result.actions],
            "notes": result.notes,
        }
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"· 快照已写入 {args.json}", file=sys.stderr)

    channel = get_channel(args.push, directory=args.out or "reports", stem="sentry")
    if args.push not in ("console", "stdout"):
        receipt = channel.send(Message(subject=subject, body=push_text, markdown=markdown))
        print(f"· 推送完成：{receipt}", file=sys.stderr)
        if args.show_report:
            print(markdown)
    else:
        print(markdown if args.full else push_text)

    if args.execute_claims:
        return _execute_claims(snapshot)

    return 0


def _execute_claims(snapshot) -> int:
    """Opt-in, zero-risk write: claim every coupon available this cycle."""
    if snapshot.source != "live":
        print("· 演示模式：跳过真实领券。", file=sys.stderr)
        return 0
    if not snapshot.claimable:
        print("· 当前无可领券，跳过写入。", file=sys.stderr)
        return 0
    config = load_config()
    with McpClient(config.token, config.endpoint) as client:
        result = client.call("auto-bind-coupons")
    state = "成功" if result.ok else "失败"
    print(f"· 自动领券{state}：{(result.text or result.error or '')[:200]}", file=sys.stderr)
    return 0 if result.ok else 1


def cmd_probe(args) -> int:
    config = load_config(args.token, args.endpoint)
    tools = [
        "now-time-info",
        "query-my-account",
        "query-my-coupons",
        "available-coupons",
        "query-lottery-info",
        "query-my-prizes",
        "order-list",
        "mall-points-products",
        "campaign-calendar",
    ]
    print(f"麦当劳 MCP 探针（{config.endpoint}）\n")
    print("| 工具 | 结果 | 摘要 |")
    print("|---|---|---|")
    ok_count = 0
    with McpClient(config.token, config.endpoint) as client:
        for name in tools:
            result = client.call(name)
            ok_count += 1 if result.ok else 0
            summary = _summarize(result)
            mark = "✅" if result.ok else "❌"
            print(f"| `{name}` | {mark} | {summary} |")
    print(f"\n{ok_count}/{len(tools)} 个只读工具可用。")
    return 0


def _summarize(result, limit: int = 78) -> str:
    """A one-line gist of a tool response for the probe table.

    Most tools prefix a long "API Response Information" explainer before the
    real payload, which would push the useful part out of view. Prefer the
    parsed JSON when there is one.
    """
    from sentry import normalize as nz

    payload = nz.extract_json(result.text)
    if isinstance(payload, (dict, list)) and payload:
        if isinstance(payload, dict):
            noisy = {"success", "code", "message", "datetime", "traceId"}
            payload = {k: v for k, v in payload.items() if k not in noisy} or payload
        text = json.dumps(payload, ensure_ascii=False)
    else:
        text = result.text or result.error or ""
    text = " ".join(text.split()).replace("|", "/")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def cmd_version(_args) -> int:
    print(f"McAccount Sentinel v{__version__}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mcsentry",
        description="麦麦账户哨兵：无人值守的麦当劳账户资产巡检与清算",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--token", help="MCP Token（默认读环境变量 MCD_MCP_TOKEN）")
    common.add_argument("--endpoint", help="MCP 接入地址（默认 https://mcp.mcd.cn）")

    patrol = sub.add_parser("patrol", parents=[common], help="执行一次巡检")
    patrol.add_argument("--demo", action="store_true", help="使用内置演示数据，无需 Token")
    patrol.add_argument(
        "--push", default="console", help="推送通道：console / file / email（默认 console）"
    )
    patrol.add_argument("--out", help="报告目录（file 通道使用）")
    patrol.add_argument("--json", help="把原始快照+评估结果写入指定 JSON 文件")
    patrol.add_argument("--full", action="store_true", help="控制台输出完整 markdown 报告")
    patrol.add_argument("--show-report", action="store_true", help="推送后额外打印完整报告")
    patrol.add_argument("--no-catalogue", action="store_true", help="跳过积分商城（省一次调用）")
    patrol.add_argument(
        "--execute-claims",
        action="store_true",
        help="授权执行零风险写操作（自动领取可领券）。默认关闭。",
    )
    patrol.set_defaults(func=cmd_patrol)

    probe = sub.add_parser("probe", parents=[common], help="检查各 MCP 工具连通性")
    probe.set_defaults(func=cmd_probe)

    version_cmd = sub.add_parser("version", help="显示版本")
    version_cmd.set_defaults(func=cmd_version)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except McpError as exc:
        print(f"✗ MCP 调用失败: {exc}", file=sys.stderr)
        return 1
    except DeliveryError as exc:
        print(f"✗ 推送失败: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
