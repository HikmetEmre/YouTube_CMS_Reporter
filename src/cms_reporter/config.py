"""Network configuration (producers, mapping rules, rates) and runtime settings."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

SHORTS_MODES = ("pool", "per_channel")


class ConfigError(ValueError):
    pass


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def normalize_id(value: object) -> str:
    """Canonical form for Custom IDs and patterns: lowercase, no whitespace."""
    if value is None or value != value:  # None or NaN
        return ""
    return re.sub(r"\s+", "", str(value)).lower()


@dataclass(frozen=True)
class Producer:
    name: str
    commission: float
    contact_email: str | None = None
    patterns: tuple[str, ...] = ()
    labels: tuple[str, ...] = ()
    channels: tuple[str, ...] = ()

    @property
    def slug(self) -> str:
        return slugify(self.name)


@dataclass(frozen=True)
class Vertical:
    key: str
    label: str
    shorts_mode: str
    producers: tuple[Producer, ...]

    def producer(self, name: str) -> Producer | None:
        return next((p for p in self.producers if p.name == name), None)

    @property
    def producer_names(self) -> list[str]:
        return [p.name for p in self.producers]


@dataclass(frozen=True)
class NetworkConfig:
    name: str
    local_currency: str
    withholding: dict[str, float]
    verticals: dict[str, Vertical]

    @property
    def producer_names(self) -> list[str]:
        """Every client, once, in config order."""
        seen: dict[str, None] = {}
        for vertical in self.verticals.values():
            for producer in vertical.producers:
                seen.setdefault(producer.name)
        return list(seen)

    def producer_entries(self, name: str) -> dict[str, Producer]:
        """A client's entry in each vertical it appears in."""
        return {k: p for k, v in self.verticals.items() if (p := v.producer(name))}

    def channels_of(self, name: str) -> list[str]:
        out: dict[str, None] = {}
        for producer in self.producer_entries(name).values():
            for channel in producer.channels:
                out.setdefault(channel)
        return list(out)

    def contact_email(self, name: str) -> str | None:
        return next((p.contact_email for p in self.producer_entries(name).values() if p.contact_email), None)


def _producer(raw: dict, where: str) -> Producer:
    try:
        name = str(raw["name"]).strip()
        commission = float(raw["commission"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ConfigError(f"{where}: every producer needs a name and a numeric commission") from exc
    if not 0 <= commission <= 1:
        raise ConfigError(f"{where}: commission for {name!r} must be between 0 and 1, got {commission}")
    patterns = tuple(p for p in (normalize_id(x) for x in raw.get("custom_id_patterns") or []) if p)
    labels = tuple(str(x).strip() for x in raw.get("asset_labels") or [])
    channels = tuple(str(x).strip() for x in raw.get("channels") or [])
    return Producer(name, commission, raw.get("contact_email"), patterns, labels, channels)


def load_config(path: str | Path) -> NetworkConfig:
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"Network config not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    network = raw.get("network") or {}
    verticals: dict[str, Vertical] = {}
    for key, body in (raw.get("verticals") or {}).items():
        mode = body.get("shorts_mode", "pool")
        if mode not in SHORTS_MODES:
            raise ConfigError(f"verticals.{key}.shorts_mode must be one of {SHORTS_MODES}, got {mode!r}")
        producers = tuple(_producer(p, f"verticals.{key}") for p in body.get("producers") or [])
        names = [p.name for p in producers]
        if dupes := {n for n in names if names.count(n) > 1}:
            raise ConfigError(f"verticals.{key}: duplicate producers {sorted(dupes)}")
        labels: dict[str, str] = {}
        for producer in producers:
            for label in producer.labels:
                owner = labels.setdefault(label.casefold(), producer.name)
                if owner != producer.name:
                    raise ConfigError(
                        f"verticals.{key}: asset label {label!r} is claimed by both {owner!r} and {producer.name!r}"
                    )
        verticals[key] = Vertical(key, body.get("label", key), mode, producers)
    if not verticals:
        raise ConfigError(f"{path}: no verticals defined")
    return NetworkConfig(
        name=network.get("name", "Network"),
        local_currency=network.get("local_currency", "USD"),
        withholding={str(k).upper(): float(v) for k, v in (raw.get("withholding_tax") or {}).items()},
        verticals=verticals,
    )


@dataclass(frozen=True)
class Settings:
    """Where things live. Everything comes from the environment so Docker can inject it."""

    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("CMS_DATA_DIR", "data")))
    config_path: Path = field(default_factory=lambda: Path(os.environ.get("CMS_CONFIG", "config/network.yaml")))

    @property
    def inbox(self) -> Path:
        return self.data_dir / "inbox"

    @property
    def output(self) -> Path:
        return self.data_dir / "output"

    @property
    def store(self) -> Path:
        return self.data_dir / "store"

    @property
    def backups(self) -> Path:
        return self.data_dir / "backups"

    @property
    def conflicts_inbox(self) -> Path:
        return self.data_dir / "conflicts"

    def periods(self) -> list[str]:
        if not self.inbox.exists():
            return []
        return sorted(p.name for p in self.inbox.iterdir() if p.is_dir() and re.fullmatch(r"\d{4}-\d{2}", p.name))
