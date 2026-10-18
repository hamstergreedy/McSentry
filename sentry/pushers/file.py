"""Write the report to disk — markdown plus a machine-readable snapshot.

Useful for two things: keeping a history of what the account looked like over
time, and letting a static site publish the patrol log.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .base import Channel, DeliveryError, Message


class FileChannel(Channel):
    name = "file"

    def __init__(self, directory: str | Path = "reports", stem: str = "report", **_ignored):
        self.directory = Path(directory)
        self.stem = stem

    def send(self, message: Message) -> str:
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            path = self.directory / f"{self.stem}-{stamp}.md"
            path.write_text(message.markdown or message.body, encoding="utf-8")
            return f"written to {path}"
        except OSError as exc:
            raise DeliveryError(f"写入报告失败: {exc}") from exc
