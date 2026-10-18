"""SMTP email channel.

Configuration comes from the environment only — never from a file that could be
committed:

=====================  ==============================================
SENTRY_SMTP_HOST       e.g. ``smtp.qq.com``
SENTRY_SMTP_PORT       ``465`` (implicit TLS) or ``587`` (STARTTLS)
SENTRY_SMTP_USER       login account
SENTRY_SMTP_PASS       SMTP authorisation code, **not** the mailbox password
SENTRY_MAIL_FROM       defaults to ``SENTRY_SMTP_USER``
SENTRY_MAIL_TO         comma-separated recipients
=====================  ==============================================

Why email as the reference channel: it is the one delivery path that works from
a bare cron box with no third-party bot, no webhook receiver and no account
binding. The report is plain text on purpose so it reads correctly in a phone
notification preview.
"""

from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from .base import Channel, DeliveryError, Message


class EmailChannel(Channel):
    name = "email"

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        user: str | None = None,
        password: str | None = None,
        sender: str | None = None,
        recipients: str | list[str] | None = None,
        **_ignored,
    ):
        self.host = host or os.environ.get("SENTRY_SMTP_HOST", "")
        self.port = int(port or os.environ.get("SENTRY_SMTP_PORT", "465"))
        self.user = user or os.environ.get("SENTRY_SMTP_USER", "")
        self.password = password or os.environ.get("SENTRY_SMTP_PASS", "")
        self.sender = sender or os.environ.get("SENTRY_MAIL_FROM") or self.user
        raw = recipients if recipients is not None else os.environ.get("SENTRY_MAIL_TO", "")
        if isinstance(raw, str):
            self.recipients = [r.strip() for r in raw.split(",") if r.strip()]
        else:
            self.recipients = [str(r).strip() for r in raw if str(r).strip()]

    def validate(self) -> None:
        missing = [
            key
            for key, val in (
                ("SENTRY_SMTP_HOST", self.host),
                ("SENTRY_SMTP_USER", self.user),
                ("SENTRY_SMTP_PASS", self.password),
                ("SENTRY_MAIL_TO", self.recipients),
            )
            if not val
        ]
        if missing:
            raise DeliveryError("邮件通道缺少配置: " + ", ".join(missing))

    def send(self, message: Message) -> str:
        self.validate()
        mail = EmailMessage()
        mail["Subject"] = message.subject
        mail["From"] = self.sender
        mail["To"] = ", ".join(self.recipients)
        mail["Date"] = formatdate(localtime=True)
        mail["Message-ID"] = make_msgid(domain="mcd-sentry.local")
        mail.set_content(message.body)

        context = ssl.create_default_context()
        try:
            if self.port == 465:
                with smtplib.SMTP_SSL(self.host, self.port, context=context, timeout=30) as smtp:
                    smtp.login(self.user, self.password)
                    smtp.send_message(mail)
            else:
                with smtplib.SMTP(self.host, self.port, timeout=30) as smtp:
                    smtp.ehlo()
                    smtp.starttls(context=context)
                    smtp.ehlo()
                    smtp.login(self.user, self.password)
                    smtp.send_message(mail)
        except (smtplib.SMTPException, OSError) as exc:
            raise DeliveryError(f"邮件发送失败: {exc}") from exc
        return f"sent to {len(self.recipients)} recipient(s) via {self.host}:{self.port}"
