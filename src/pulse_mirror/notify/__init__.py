"""Pluggable notifications. Email (SMTP) is the first backend; others implement Notifier."""

from __future__ import annotations

import logging
import os
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Protocol

log = logging.getLogger(__name__)


class Notifier(Protocol):
    def send(self, subject: str, text: str) -> None: ...


@dataclass(frozen=True)
class EmailNotifier:
    host: str
    port: int
    user: str | None
    password: str | None
    sender: str
    recipient: str
    security: str = "starttls"  # starttls | ssl | none

    def send(self, subject: str, text: str) -> None:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = self.sender
        msg["To"] = self.recipient
        msg.set_content(text)
        context = ssl.create_default_context()
        smtp: smtplib.SMTP
        if self.security == "ssl":
            smtp = smtplib.SMTP_SSL(self.host, self.port, context=context, timeout=30)
        else:
            smtp = smtplib.SMTP(self.host, self.port, timeout=30)
        with smtp:
            if self.security == "starttls":
                smtp.starttls(context=context)
            if self.user and self.password:
                smtp.login(self.user, self.password)
            smtp.send_message(msg)


def from_env() -> Notifier | None:
    """Build the configured notifier from PULSE_SMTP_* variables, or None if unset."""
    host = os.environ.get("PULSE_SMTP_HOST", "").strip()
    recipient = os.environ.get("PULSE_NOTIFY_TO", "").strip()
    if not host or not recipient:
        return None
    user = os.environ.get("PULSE_SMTP_USER") or None
    return EmailNotifier(
        host=host,
        port=int(os.environ.get("PULSE_SMTP_PORT", "587")),
        user=user,
        password=os.environ.get("PULSE_SMTP_PASSWORD") or None,
        sender=os.environ.get("PULSE_SMTP_FROM") or user or recipient,
        recipient=recipient,
        security=os.environ.get("PULSE_SMTP_SECURITY", "starttls").lower(),
    )
