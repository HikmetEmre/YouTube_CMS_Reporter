"""E-mail each client its monthly package. Dry run unless told to send."""

from __future__ import annotations

import os
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path

import pandas as pd

from .config import NetworkConfig, Settings, slugify
from .conflicts import smtp_send

MIME = {
    ".pdf": ("application", "pdf"),
    ".zip": ("application", "zip"),
    ".xlsx": ("application", "vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
}


@dataclass
class Delivery:
    producer: str
    recipient: str | None
    files: list[Path]
    status: str = "planned"


def plan(settings: Settings, cfg: NetworkConfig, period: str) -> list[Delivery]:
    clients = settings.output / period / "clients"
    out = []
    for producer in cfg.producer_names:
        folder = clients / slugify(producer)
        files = sorted(folder.glob("*")) if folder.exists() else []
        if not files:
            continue
        recipient = cfg.contact_email(producer)
        out.append(Delivery(producer, recipient, files, "planned" if recipient else "skipped: no contact_email"))
    return out


def deliver(
    settings: Settings, cfg: NetworkConfig, period: str, send: bool = False, only: str | None = None
) -> list[Delivery]:
    month = pd.Period(period, freq="M").strftime("%B %Y")
    deliveries = [d for d in plan(settings, cfg, period) if only in (None, d.producer)]
    if not send:
        return deliveries
    for item in deliveries:
        if not item.recipient:
            continue
        message = EmailMessage()
        message["Subject"] = f"{cfg.name} - royalty statement {month}"
        message["To"] = item.recipient
        message["From"] = os.environ.get("SMTP_FROM") or os.environ["SMTP_USER"]
        message.set_content(
            f"Hello,\n\nPlease find attached your {month} statement from {cfg.name}: the report (PDF) and "
            "the underlying revenue data (Excel and CSV).\n\nThe files are password protected. Your password "
            "is the one we shared with you separately; it does not change from month to month.\n\n"
            f"Kind regards,\n{cfg.name}\n"
        )
        for path in item.files:
            main, sub = MIME.get(path.suffix, ("application", "octet-stream"))
            message.add_attachment(path.read_bytes(), maintype=main, subtype=sub, filename=path.name)
        try:
            smtp_send(message)
            item.status = "sent"
        except Exception as exc:
            item.status = f"failed: {exc}"
    return deliveries
