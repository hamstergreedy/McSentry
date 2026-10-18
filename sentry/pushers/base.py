"""Channel contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


class DeliveryError(RuntimeError):
    pass


@dataclass
class Message:
    subject: str
    body: str
    markdown: str | None = None


class Channel(ABC):
    name = "base"

    @abstractmethod
    def send(self, message: Message) -> str:
        """Deliver the message. Returns a human-readable receipt."""
