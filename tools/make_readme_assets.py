#!/usr/bin/env python3
"""生成 README 首屏用的终端截图 SVG。

图内所有文字都来自 `python mcsentry.py patrol --demo` 的真实输出，
只做了两处处理：删除空行以外的多余缩进、裁掉次要行。
改了程序输出后重跑本脚本即可同步，避免图和代码说法不一致。

    python tools/make_readme_assets.py
"""

from __future__ import annotations

import pathlib
from xml.sax.saxutils import escape

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "demo-terminal.svg"

# 配色取自 GitHub Dark，保证在浅色主题下也自成一个"终端窗口"
C_BG = "#0d1117"
C_BAR = "#161b22"
C_BORDER = "#30363d"
C_DIM = "#6e7681"
C_TEXT = "#c9d1d9"
C_HEAD = "#e6edf3"
C_MONEY = "#f2cc60"
C_LOSS = "#ff7b72"
C_GAIN = "#3fb950"
C_PROMPT = "#7ee787"

FONT = (
    "'SFMono-Regular', Menlo, Consolas, 'Cascadia Mono', "
    "'Liberation Mono', 'Microsoft YaHei', monospace"
)

# 每行是若干 (文字, 颜色) 分段；空列表表示空行
LINES: list[list[tuple[str, str]]] = [
    [("$ ", C_PROMPT), ("python mcsentry.py patrol --demo", C_HEAD)],
    [],
    [("🍟 麦麦账户哨兵 · 10-18 20:17", C_HEAD)],
    [],
    [
        ("到 11-30 为止，你有 ", C_TEXT),
        ("3", C_MONEY),
        (" 项资产共约 ", C_TEXT),
        ("¥132.72", C_MONEY),
        (" 会清零；", C_TEXT),
    ],
    [
        ("最大一笔是「本月将过期：", C_TEXT),
        ("640 积分", C_MONEY),
        ("」，13 天后到期。", C_TEXT),
    ],
    [],
    [("⏳ 会被清零的：", C_HEAD)],
    [("1. 下单立减3元券", C_TEXT), ("（¥3.00，10-21 23:59 前）", C_DIM)],
    [("2. 麦辣三件套5折券", C_TEXT), ("（10-27 23:59 前）", C_DIM)],
    [
        ("3. 本月将过期：640 积分", C_TEXT),
        ("（¥88.32，10-31 23:59 前）", C_MONEY),
    ],
    [
        ("4. 下月将过期：300 积分", C_TEXT),
        ("（¥41.40，11-30 23:59 前）", C_MONEY),
    ],
    [],
    [("✅ 零成本可做的：", C_HEAD)],
    [("1. 可领券 3 张", C_TEXT), ("（¥7.00）", C_DIM)],
    [("2. 甜辣小食组合5折券", C_TEXT), ("（持有券）", C_DIM)],
    [("3. 可用抽奖次数 2 次", C_TEXT)],
    [],
    [
        ("⚠️ 已经蒸发的部分：", C_LOSS),
        ("1500 积分（占总累计 27%），折合约 ", C_TEXT),
        ("¥207.00", C_LOSS),
        ("。", C_TEXT),
    ],
    [],
    [
        ("💡 积分购买力实测：", C_TEXT),
        ("¥0.027 ~ ¥0.138/分", C_GAIN),
        ("，最优换法是「6.9元可乐麦炫酷」。", C_TEXT),
    ],
    [],
    [("—— 麦麦账户哨兵 · 无人值守巡检", C_DIM)],
    [],
    [("$ ", C_PROMPT), ("▊", C_DIM)],
]

WIDTH = 780
BAR_H = 36
PAD_TOP = 22
PAD_BOTTOM = 20
LINE_H = 21
FONT_SIZE = 13.5
LEFT = 26


def build() -> str:
    height = BAR_H + PAD_TOP + len(LINES) * LINE_H + PAD_BOTTOM
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {height}" '
        f'width="{WIDTH}" height="{height}" role="img" '
        f'aria-label="麦麦账户哨兵演示模式终端输出">',
        "<title>麦麦账户哨兵 · 演示模式输出</title>",
        "<desc>终端窗口内显示 python mcsentry.py patrol --demo 的巡检结果</desc>",
        f'<rect x="0.5" y="0.5" width="{WIDTH - 1}" height="{height - 1}" rx="10" '
        f'fill="{C_BG}" stroke="{C_BORDER}"/>',
        # 标题栏
        f'<path d="M0.5 10.5 A10 10 0 0 1 10.5 0.5 H{WIDTH - 10.5} '
        f'A10 10 0 0 1 {WIDTH - 0.5} 10.5 V{BAR_H} H0.5 Z" fill="{C_BAR}"/>',
        f'<line x1="0.5" y1="{BAR_H}" x2="{WIDTH - 0.5}" y2="{BAR_H}" stroke="{C_BORDER}"/>',
    ]
    for idx, colour in enumerate(("#ff5f56", "#ffbd2e", "#27c93f")):
        cx = 22 + idx * 20
        parts.append(f'<circle cx="{cx}" cy="{BAR_H / 2}" r="5.5" fill="{colour}"/>')
    parts.append(
        f'<text x="{WIDTH / 2}" y="{BAR_H / 2 + 4}" text-anchor="middle" '
        f'font-family="{FONT}" font-size="12" fill="{C_DIM}">'
        f"mcsentry — patrol --demo</text>"
    )

    y = BAR_H + PAD_TOP + FONT_SIZE
    for line in LINES:
        if line:
            spans = []
            for text, colour in line:
                spans.append(f'<tspan fill="{colour}">{escape(text)}</tspan>')
            weight = ' font-weight="600"' if line[0][1] == C_HEAD else ""
            parts.append(
                f'<text x="{LEFT}" y="{y:.1f}" font-family="{FONT}" '
                f'font-size="{FONT_SIZE}"{weight}>{"".join(spans)}</text>'
            )
        y += LINE_H

    parts.append("</svg>")
    return "\n".join(parts) + "\n"


if __name__ == "__main__":
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(build(), encoding="utf-8")
    print(f"已生成 {OUT.relative_to(ROOT)}（{OUT.stat().st_size} 字节）")
