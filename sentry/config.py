"""Configuration and token resolution.

Token lookup order (first hit wins):
1. ``--token`` CLI flag
2. ``MCD_MCP_TOKEN`` environment variable
3. ``.env`` file in the project root (``MCD_MCP_TOKEN=...``)
4. ``~/.workbuddy/mcp.json`` (the WorkBuddy connector config, if present)

Only the first three are documented as supported paths for third-party users.
The fourth is a local convenience for WorkBuddy users who already configured
the connector and do not want to paste the token twice.

The token is a personal credential. It is never logged, never rendered into a
report, and never written into the report artefacts.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENDPOINT = "https://mcp.mcd.cn"


@dataclass
class Config:
    token: str
    endpoint: str = DEFAULT_ENDPOINT
    timeout: float = 45.0
    # Timezone offset in hours used for deadline math. McDonald's China runs on
    # UTC+8 and every timestamp the API returns is already local time.
    tz_offset_hours: int = 8


def _read_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        values[key.strip()] = val.strip().strip("'\"")
    return values


def _read_workbuddy_config() -> str | None:
    path = Path.home() / ".workbuddy" / "mcp.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    server = (data.get("mcpServers") or {}).get("mcd-mcp") or {}
    raw = str((server.get("headers") or {}).get("Authorization", ""))
    return raw.split(None, 1)[1].strip() if raw.lower().startswith("bearer ") else (raw.strip() or None)


def resolve_token(cli_token: str | None = None) -> str:
    if cli_token:
        return cli_token
    if os.environ.get("MCD_MCP_TOKEN"):
        return os.environ["MCD_MCP_TOKEN"].strip()
    dotenv = _read_dotenv(PROJECT_ROOT / ".env")
    if dotenv.get("MCD_MCP_TOKEN"):
        return dotenv["MCD_MCP_TOKEN"].strip()
    found = _read_workbuddy_config()
    if found:
        return found
    raise SystemExit(
        "未找到麦当劳 MCP Token。请设置环境变量 MCD_MCP_TOKEN，或在项目根目录创建 .env 文件。\n"
        "申请地址：https://open.mcd.cn/mcp （手机号登录 → 控制台 → 激活）"
    )


def load_config(cli_token: str | None = None, endpoint: str | None = None) -> Config:
    return Config(
        token=resolve_token(cli_token),
        endpoint=(endpoint or os.environ.get("MCD_MCP_ENDPOINT") or DEFAULT_ENDPOINT),
    )


def mask(token: str) -> str:
    """Render a token safely for logs."""
    if len(token) <= 8:
        return "*" * len(token)
    return f"{token[:4]}{'*' * (len(token) - 8)}{token[-4:]}"
