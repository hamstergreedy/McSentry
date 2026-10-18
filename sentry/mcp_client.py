"""Minimal MCP client for McDonald's China MCP Server (Streamable HTTP).

Design notes
------------
* Standard library only. No pip install required, so the sentry can run on a
  bare cron box or inside a CI job.
* We deliberately talk JSON-RPC by hand instead of using an SDK. Reason: several
  of McDonald's tools ship an ``outputSchema`` that does not match what the
  server actually returns (see ``normalize.py`` and MCP_INTEGRATION.md for the
  documented cases). SDK clients validate structured content against that
  schema and surface ``-32602`` as a hard error, which throws away perfectly
  usable data. Reading the raw ``content[].text`` payload lets the sentry
  normalize defensively instead of failing the whole patrol.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

DEFAULT_ENDPOINT = "https://mcp.mcd.cn"
CLIENT_NAME = "mcd-account-sentry"
CLIENT_VERSION = "0.1.0"

# Streamable HTTP was introduced in 2025-03-26. Servers are expected to reply
# with their own negotiated version; we try newest-first and keep whatever the
# server answers.
PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")


class McpError(RuntimeError):
    """Raised when the MCP server rejects a request."""

    def __init__(self, message: str, *, code: int | None = None, data: Any = None):
        super().__init__(message)
        self.code = code
        self.data = data


@dataclass
class ToolResult:
    """Outcome of a single ``tools/call``."""

    name: str
    ok: bool
    text: str = ""
    structured: Any = None
    error: str | None = None
    payload: Any = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        if self.payload is None:
            return not self.text.strip()
        return False


class McpClient:
    """A tiny, dependency-free MCP client speaking Streamable HTTP."""

    def __init__(
        self,
        token: str,
        endpoint: str = DEFAULT_ENDPOINT,
        *,
        timeout: float = 45.0,
        user_agent: str = f"{CLIENT_NAME}/{CLIENT_VERSION}",
    ) -> None:
        if not token or not token.strip():
            raise ValueError("MCP token is empty")
        self.token = token.strip()
        self.endpoint = endpoint.rstrip("/")
        self.timeout = timeout
        self.user_agent = user_agent
        self.session_id: str | None = None
        self.protocol_version: str | None = None
        self._next_id = 0
        self._initialized = False

    # ------------------------------------------------------------------ http

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {self.token}",
            "User-Agent": self.user_agent,
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        if self.protocol_version:
            headers["MCP-Protocol-Version"] = self.protocol_version
        return headers

    def _post(self, body: dict[str, Any]) -> tuple[int, dict[str, str], str]:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self.endpoint, data=data, headers=self._headers(), method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                return resp.status, dict(resp.headers), raw
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            return exc.code, dict(exc.headers or {}), raw
        except (urllib.error.URLError, socket.timeout) as exc:
            raise McpError(f"transport failure: {exc}") from exc

    @staticmethod
    def _parse_body(body: str, content_type: str) -> Any:
        """Extract the JSON-RPC envelope from either JSON or SSE framing."""
        body = body.strip()
        if not body:
            return None

        if "text/event-stream" in content_type or body.startswith(("event:", "data:", ":")):
            messages: list[Any] = []
            for line in body.splitlines():
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                chunk = line[5:].strip()
                if not chunk or chunk == "[DONE]":
                    continue
                try:
                    messages.append(json.loads(chunk))
                except json.JSONDecodeError:
                    continue
            # A response to our request is the one carrying "id" and either
            # "result" or "error".
            for msg in reversed(messages):
                if isinstance(msg, dict) and ("result" in msg or "error" in msg):
                    return msg
            return messages[-1] if messages else None

        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return None

    def _rpc(self, method: str, params: dict[str, Any] | None = None) -> Any:
        self._next_id += 1
        body: dict[str, Any] = {"jsonrpc": "2.0", "id": self._next_id, "method": method}
        if params is not None:
            body["params"] = params

        status, headers, raw = self._post(body)
        if status == 401:
            raise McpError("401 MCP Token invalid or expired", code=401)
        if status == 429:
            raise McpError("429 rate limited (600 req/min)", code=429)

        envelope = self._parse_body(raw, headers.get("Content-Type", ""))
        if envelope is None:
            raise McpError(f"empty or unparsable response (HTTP {status}): {raw[:300]}")
        if isinstance(envelope, list):
            envelope = next(
                (m for m in envelope if isinstance(m, dict) and "result" in m), envelope[0]
            )
        if not isinstance(envelope, dict):
            raise McpError(f"unexpected envelope: {envelope!r}")

        if "error" in envelope and envelope["error"]:
            err = envelope["error"]
            raise McpError(
                str(err.get("message", err)), code=err.get("code"), data=err.get("data")
            )
        return envelope.get("result")

    # -------------------------------------------------------------- lifecycle

    def initialize(self) -> dict[str, Any]:
        last_error: Exception | None = None
        for version in PROTOCOL_VERSIONS:
            try:
                status, headers, raw = self._post(
                    {
                        "jsonrpc": "2.0",
                        "id": 0,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": version,
                            "capabilities": {},
                            "clientInfo": {"name": CLIENT_NAME, "version": CLIENT_VERSION},
                        },
                    }
                )
                envelope = self._parse_body(raw, headers.get("Content-Type", ""))
                if isinstance(envelope, dict) and envelope.get("error"):
                    last_error = McpError(str(envelope["error"]))
                    continue
                result = (envelope or {}).get("result") if isinstance(envelope, dict) else None
                if not result:
                    last_error = McpError(f"initialize failed (HTTP {status})")
                    continue

                self.session_id = headers.get("Mcp-Session-Id") or headers.get(
                    "mcp-session-id"
                )
                self.protocol_version = result.get("protocolVersion", version)
                self._notify_initialized()
                self._initialized = True
                return result
            except McpError as exc:  # try the next protocol version
                last_error = exc
        raise McpError(f"initialize failed for all protocol versions: {last_error}")

    def _notify_initialized(self) -> None:
        body = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        try:
            self._post(body)
        except McpError:
            # Notification has no reply; a failure here is not fatal.
            pass

    def ensure_ready(self) -> None:
        if not self._initialized:
            self.initialize()

    # ------------------------------------------------------------------ tools

    def list_tools(self) -> list[dict[str, Any]]:
        self.ensure_ready()
        result = self._rpc("tools/list")
        return list((result or {}).get("tools", []))

    def call(self, name: str, arguments: dict[str, Any] | None = None) -> ToolResult:
        self.ensure_ready()
        try:
            result = self._rpc("tools/call", {"name": name, "arguments": arguments or {}})
        except McpError as exc:
            return ToolResult(name=name, ok=False, error=str(exc), raw={"error": str(exc)})

        result = result or {}
        content = result.get("content") or []
        text_parts: list[str] = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                text_parts.append(str(part.get("text", "")))
            elif isinstance(part, str):
                text_parts.append(part)

        return ToolResult(
            name=name,
            ok=not result.get("isError", False),
            text="\n".join(text_parts).strip(),
            structured=result.get("structuredContent"),
            error=(result.get("isError") and "\n".join(text_parts)) or None,
            raw=result,
        )

    def close(self) -> None:
        if not self.session_id:
            return
        try:
            req = urllib.request.Request(
                self.endpoint, headers=self._headers(), method="DELETE"
            )
            urllib.request.urlopen(req, timeout=5).close()
        except Exception:  # best effort
            pass
        finally:
            self.session_id = None

    def __enter__(self) -> "McpClient":
        self.initialize()
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()
