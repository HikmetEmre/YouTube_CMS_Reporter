"""Where a request's data lives.

Self-hosted: one workspace, the configured data folder and network config.

Demo mode (CMS_DEMO_MODE=1, for a public instance): every visitor gets a private
copy of a synthetic seed workspace, keyed by a session cookie and deleted after
a few hours. Visitors can upload files and change rules without seeing each
other's data, and nothing they do reaches a real mailbox or API.
"""

from __future__ import annotations

import os
import re
import secrets
import shutil
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import yaml

from . import store, synth
from .config import ConfigError, NetworkConfig, Settings, load_config
from .pipeline import run_period

SESSION_TTL = int(os.environ.get("CMS_SESSION_TTL", 2 * 3600))
MAX_SESSIONS = int(os.environ.get("CMS_MAX_SESSIONS", 150))
SEED_MONTHS = 6
_lock = threading.Lock()
_last_purge = 0.0


def demo_mode() -> bool:
    return os.environ.get("CMS_DEMO_MODE", "").lower() in {"1", "true", "yes"}


@dataclass
class Workspace:
    settings: Settings
    demo: bool = False
    session: str | None = None

    @property
    def cfg(self) -> NetworkConfig:
        return load_config(self.settings.config_path)

    @property
    def expires_at(self) -> float | None:
        if not self.session:
            return None
        return self.settings.data_dir.parent.stat().st_mtime + SESSION_TTL


def _sessions_root() -> Path:
    return Settings().data_dir / "sessions"


def _seed_root() -> Path:
    return Settings().data_dir / "seed"


def _settings_in(root: Path) -> Settings:
    return Settings(data_dir=root / "data", config_path=root / "network.yaml")


def ensure_seed() -> Path:
    """Build the synthetic workspace every demo session starts from (once)."""
    root = _seed_root()
    with _lock:
        if (root / ".ready").exists():
            return root
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True)
        shutil.copyfile(Settings().config_path, root / "network.yaml")
        settings = _settings_in(root)
        cfg = load_config(settings.config_path)
        last = str(pd.Period.now("M") - 1)
        for period in synth.recent_periods(last, SEED_MONTHS):
            synth.generate_period(settings, cfg, period)
            result = run_period(settings, cfg, period)
            store.save_history(settings.store, period, {k: v.settlement for k, v in result.verticals.items()})
        (root / ".ready").write_text("ok")
    return root


def new_session_id() -> str:
    return secrets.token_urlsafe(24)


def purge_expired(now: float | None = None) -> None:
    global _last_purge
    now = now or time.time()
    if now - _last_purge < 60:
        return
    _last_purge = now
    root = _sessions_root()
    if not root.exists():
        return
    folders = sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime)
    stale = [p for p in folders if now - p.stat().st_mtime > SESSION_TTL]
    overflow = [p for p in folders if p not in stale][: max(len(folders) - len(stale) - MAX_SESSIONS, 0)]
    for folder in (*stale, *overflow):
        shutil.rmtree(folder, ignore_errors=True)


def session_workspace(session: str, reset: bool = False) -> Workspace:
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,64}", session):
        raise ValueError("bad session id")
    root = _sessions_root() / session
    if reset:
        shutil.rmtree(root, ignore_errors=True)
    if not (root / "network.yaml").exists():
        shutil.copytree(ensure_seed(), root, dirs_exist_ok=True)
    os.utime(root)  # activity keeps a session alive
    return Workspace(_settings_in(root), demo=True, session=session)


def default_workspace() -> Workspace:
    return Workspace(Settings())


def workspace_size(ws: Workspace) -> int:
    return sum(p.stat().st_size for p in ws.settings.inbox.rglob("*") if p.is_file())


def edit_config(path: Path, mutate: Callable[[dict], None]) -> NetworkConfig:
    """Apply a change to the network config file, keeping it only if the result is valid."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    mutate(raw)
    candidate = path.with_suffix(".yaml.tmp")
    candidate.write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8")
    try:
        cfg = load_config(candidate)
    except ConfigError:
        candidate.unlink(missing_ok=True)
        raise
    candidate.replace(path)
    return cfg


def _producer_entry(raw: dict, vertical: str, name: str) -> dict:
    producers = raw.get("verticals", {}).get(vertical, {}).get("producers")
    if producers is None:
        raise ConfigError(f"Unknown vertical {vertical!r}")
    for entry in producers:
        if entry.get("name") == name:
            return entry
    raise ConfigError(f"{name!r} is not a client in {vertical}")


def add_producer(path: Path, vertical: str, name: str, commission: float, email: str | None = None) -> NetworkConfig:
    def mutate(raw: dict) -> None:
        body = raw.get("verticals", {}).get(vertical)
        if body is None:
            raise ConfigError(f"Unknown vertical {vertical!r}")
        body.setdefault("producers", []).append(
            {"name": name.strip(), "commission": commission, **({"contact_email": email} if email else {})}
        )

    if not name.strip():
        raise ConfigError("A client needs a name")
    return edit_config(path, mutate)


RULE_FIELDS = {"label": "asset_labels", "pattern": "custom_id_patterns", "channel": "channels"}


def add_rule(path: Path, vertical: str, producer: str, kind: str, value: str) -> NetworkConfig:
    """Teach the config a new way to recognise a client's assets."""
    if kind not in RULE_FIELDS or not value.strip():
        raise ConfigError("A rule needs a kind (label, pattern or channel) and a value")

    def mutate(raw: dict) -> None:
        values = _producer_entry(raw, vertical, producer).setdefault(RULE_FIELDS[kind], [])
        if value.strip() not in values:
            values.append(value.strip())

    return edit_config(path, mutate)
