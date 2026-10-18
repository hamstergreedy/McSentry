"""Delivery channels for the patrol result.

A channel is anything that can take a subject + plain/markdown body and put it
somewhere the user will actually see it. Channels are pluggable on purpose:
the sentry's value is the patrol, not the transport. Swapping email for a
webhook or an internal chat bot should be a five-line subclass, not a rewrite.
"""

from __future__ import annotations

from .base import Channel, DeliveryError
from .console import ConsoleChannel
from .file import FileChannel

__all__ = ["Channel", "DeliveryError", "ConsoleChannel", "FileChannel", "get_channel"]


def get_channel(name: str, **kwargs):
    name = (name or "console").strip().lower()
    if name in ("console", "stdout"):
        return ConsoleChannel(**kwargs)
    if name in ("file", "markdown"):
        return FileChannel(**kwargs)
    if name in ("email", "smtp", "mail"):
        from .email import EmailChannel

        return EmailChannel(**kwargs)
    raise DeliveryError(f"未知推送通道: {name}（可选：console / file / email）")
