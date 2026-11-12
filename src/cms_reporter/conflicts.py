"""Copyright / licence conflict alerts.

YouTube tells a network about ownership conflicts, disputes and takedowns by
e-mail and in CMS, where they are easy to miss until a deadline has passed.
This module reads conflicts from one or more sources, remembers which ones it
has already announced, and pushes the new ones to chat (Telegram, WhatsApp) or
e-mail.

Sources
  * CsvFolderSource - issue exports dropped into data/conflicts/
  * ImapSource      - YouTube's own notification e-mails in a mailbox
"""

from __future__ import annotations

import email
import imaplib
import json
import os
import smtplib
import time
from dataclasses import asdict, dataclass
from email.header import decode_header, make_header
from email.message import EmailMessage
from pathlib import Path
from typing import Protocol

import pandas as pd
import requests

from .config import Settings

# Column names accepted in an issue export (first match wins). CMS exports differ
# by issue type and over time; extend these lists rather than renaming your files.
CSV_FIELDS = {
    "id": ["Issue ID", "Conflict ID", "Claim ID", "ID"],
    "kind": ["Issue Type", "Type", "Conflict Type"],
    "asset_id": ["Asset ID"],
    "title": ["Asset Title", "Title", "Video Title"],
    "other_party": ["Other Party", "Other Owner", "Claimant", "Conflicting Owner"],
    "territories": ["Territories", "Conflicting Territories", "Countries"],
    "status": ["Status"],
    "due": ["Expires", "Due Date", "Deadline", "Action Required By"],
}
MAIL_KEYWORDS = ("ownership conflict", "copyright", "claim", "dispute", "takedown", "licence", "license")


@dataclass
class Conflict:
    id: str
    kind: str
    title: str = ""
    asset_id: str = ""
    other_party: str = ""
    territories: str = ""
    status: str = ""
    due: str = ""
    source: str = ""

    def text(self) -> str:
        lines = [f"⚠️ {self.kind or 'Conflict'}: {self.title or self.asset_id or self.id}"]
        for label, value in (
            ("Asset", self.asset_id),
            ("Other party", self.other_party),
            ("Territories", self.territories),
            ("Status", self.status),
            ("Act before", self.due),
            ("Source", self.source),
        ):
            if value:
                lines.append(f"{label}: {value}")
        return "\n".join(lines)


class ConflictSource(Protocol):
    def fetch(self) -> list[Conflict]: ...


class Notifier(Protocol):
    name: str

    def send(self, text: str) -> None: ...


class CsvFolderSource:
    def __init__(self, folder: Path):
        self.folder = folder

    def fetch(self) -> list[Conflict]:
        found: list[Conflict] = []
        for path in sorted(self.folder.glob("*.csv")) if self.folder.exists() else []:
            table = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
            pick = {field: next((c for c in names if c in table.columns), None) for field, names in CSV_FIELDS.items()}
            for index, row in enumerate(table.to_dict("records")):
                values = {field: row[column].strip() if column else "" for field, column in pick.items()}
                values["id"] = values["id"] or f"{path.name}#{values['asset_id'] or index}"
                found.append(Conflict(**values, source=path.name))
        return found


class ImapSource:
    """Unread-or-not, any message from YouTube whose subject mentions a conflict keyword."""

    def __init__(
        self, host: str, user: str, password: str, mailbox: str = "INBOX", sender: str = "youtube.com", days: int = 14
    ):
        self.host, self.user, self.password = host, user, password
        self.mailbox, self.sender, self.days = mailbox, sender, days

    def fetch(self) -> list[Conflict]:
        since = (pd.Timestamp.now() - pd.Timedelta(days=self.days)).strftime("%d-%b-%Y")
        found: list[Conflict] = []
        with imaplib.IMAP4_SSL(self.host) as box:
            box.login(self.user, self.password)
            box.select(self.mailbox, readonly=True)
            _, data = box.search(None, f'(FROM "{self.sender}" SINCE {since})')
            for number in data[0].split():
                _, parts = box.fetch(number, "(BODY.PEEK[HEADER.FIELDS (SUBJECT MESSAGE-ID DATE)])")
                header = email.message_from_bytes(parts[0][1])
                subject = str(make_header(decode_header(header.get("Subject", ""))))
                if any(word in subject.lower() for word in MAIL_KEYWORDS):
                    found.append(
                        Conflict(
                            id=header.get("Message-ID", f"mail-{number.decode()}").strip(),
                            kind="YouTube notification",
                            title=subject,
                            status=header.get("Date", ""),
                            source=f"mailbox {self.user}",
                        )
                    )
        return found


class ConsoleNotifier:
    name = "console"

    def send(self, text: str) -> None:
        print(text + "\n")


class TelegramNotifier:
    name = "telegram"

    def __init__(self, token: str, chat_id: str):
        self.token, self.chat_id = token, chat_id

    def send(self, text: str) -> None:
        response = requests.post(
            f"https://api.telegram.org/bot{self.token}/sendMessage",
            json={"chat_id": self.chat_id, "text": text},
            timeout=20,
        )
        response.raise_for_status()


class WhatsAppNotifier:
    """WhatsApp through Twilio's messaging API."""

    name = "whatsapp"

    def __init__(self, account_sid: str, auth_token: str, sender: str, recipients: list[str]):
        self.sid, self.token, self.sender, self.recipients = account_sid, auth_token, sender, recipients

    def send(self, text: str) -> None:
        for recipient in self.recipients:
            response = requests.post(
                f"https://api.twilio.com/2010-04-01/Accounts/{self.sid}/Messages.json",
                auth=(self.sid, self.token),
                data={"From": f"whatsapp:{self.sender}", "To": f"whatsapp:{recipient}", "Body": text},
                timeout=20,
            )
            response.raise_for_status()


class EmailNotifier:
    name = "email"

    def __init__(self, recipients: list[str]):
        self.recipients = recipients

    def send(self, text: str) -> None:
        message = EmailMessage()
        message["Subject"] = text.splitlines()[0][:150]
        message["From"] = os.environ.get("SMTP_FROM") or os.environ["SMTP_USER"]
        message["To"] = ", ".join(self.recipients)
        message.set_content(text)
        smtp_send(message)


def smtp_send(message: EmailMessage) -> None:
    host, port = os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT", "587"))
    with smtplib.SMTP(host, port, timeout=30) as server:
        server.starttls()
        if os.environ.get("SMTP_USER"):
            server.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASSWORD", ""))
        server.send_message(message)


def _list(name: str) -> list[str]:
    return [item.strip() for item in os.environ.get(name, "").split(",") if item.strip()]


def notifiers_from_env() -> list[Notifier]:
    """Every channel that is fully configured in the environment; console if none is."""
    env = os.environ.get
    out: list[Notifier] = []
    if env("TELEGRAM_BOT_TOKEN") and env("TELEGRAM_CHAT_ID"):
        out.append(TelegramNotifier(env("TELEGRAM_BOT_TOKEN"), env("TELEGRAM_CHAT_ID")))
    if env("TWILIO_ACCOUNT_SID") and env("TWILIO_AUTH_TOKEN") and env("TWILIO_WHATSAPP_FROM") and _list("WHATSAPP_TO"):
        out.append(
            WhatsAppNotifier(
                env("TWILIO_ACCOUNT_SID"), env("TWILIO_AUTH_TOKEN"), env("TWILIO_WHATSAPP_FROM"), _list("WHATSAPP_TO")
            )
        )
    if env("SMTP_HOST") and _list("ALERT_EMAIL_TO"):
        out.append(EmailNotifier(_list("ALERT_EMAIL_TO")))
    return out or [ConsoleNotifier()]


def sources_from_env(settings: Settings) -> list[ConflictSource]:
    env = os.environ.get
    out: list[ConflictSource] = [CsvFolderSource(settings.conflicts_inbox)]
    if env("IMAP_HOST") and env("IMAP_USER") and env("IMAP_PASSWORD"):
        out.append(
            ImapSource(
                env("IMAP_HOST"),
                env("IMAP_USER"),
                env("IMAP_PASSWORD"),
                env("IMAP_MAILBOX", "INBOX"),
                env("IMAP_SENDER", "youtube.com"),
            )
        )
    return out


def _seen_path(settings: Settings) -> Path:
    return settings.store / "conflicts_seen.json"


def read_seen(settings: Settings) -> dict[str, dict]:
    path = _seen_path(settings)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def check(settings: Settings, sources: list[ConflictSource], notifiers: list[Notifier]) -> list[Conflict]:
    """Announce conflicts not announced before. Returns the new ones.

    A conflict is only remembered once at least one notifier accepted it, so a
    failed delivery is retried on the next run.
    """
    seen = read_seen(settings)
    fresh: dict[str, Conflict] = {}
    for source in sources:
        for conflict in source.fetch():
            if conflict.id not in seen:
                fresh.setdefault(conflict.id, conflict)

    for conflict in fresh.values():
        delivered = []
        for notifier in notifiers:
            try:
                notifier.send(conflict.text())
                delivered.append(notifier.name)
            except Exception as exc:  # one broken channel must not silence the others
                print(f"[conflicts] {notifier.name} failed: {exc}")
        if delivered:
            seen[conflict.id] = {
                **asdict(conflict),
                "notified_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "via": delivered,
            }
    if fresh:
        settings.store.mkdir(parents=True, exist_ok=True)
        _seen_path(settings).write_text(json.dumps(seen, indent=2, ensure_ascii=False), encoding="utf-8")
    return list(fresh.values())


def write_demo_conflicts(settings: Settings) -> Path:
    """A synthetic issue export, for trying the notifier without CMS access."""
    settings.conflicts_inbox.mkdir(parents=True, exist_ok=True)
    path = settings.conflicts_inbox / "demo_issues.csv"
    pd.DataFrame(
        [
            [
                "ISS-DEMO-0001",
                "Ownership conflict",
                "A100000000000001",
                "Silent Harbor",
                "Example Rights Ltd",
                "DE, AT, CH",
                "Action required",
                "2026-12-01",
            ],
            [
                "ISS-DEMO-0002",
                "Disputed claim",
                "A100000000000002",
                "Golden Lanterns 4",
                "Sample Uploader",
                "Worldwide",
                "Awaiting review",
                "2026-11-28",
            ],
            ["ISS-DEMO-0003", "Invalid reference", "A100000000000003", "Paper Compass", "", "", "Action required", ""],
        ],
        columns=[
            "Issue ID",
            "Issue Type",
            "Asset ID",
            "Asset Title",
            "Other Party",
            "Territories",
            "Status",
            "Expires",
        ],
    ).to_csv(path, index=False)
    return path
