"""Print to stdout. The default channel, and the one used in CI."""

from __future__ import annotations

from .base import Channel, Message


class ConsoleChannel(Channel):
    name = "console"

    def __init__(self, stream=None, **_ignored):
        self.stream = stream

    def send(self, message: Message) -> str:
        out = self.stream or __import__("sys").stdout
        print(message.body, file=out)
        return "printed to stdout"
