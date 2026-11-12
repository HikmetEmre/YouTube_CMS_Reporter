"""Small persistent state: manual asset overrides and the settlement history."""

from __future__ import annotations

import io
from datetime import date
from pathlib import Path

import pandas as pd
from cryptography.fernet import InvalidToken

from . import security
from .settlement import MONEY

OVERRIDE_COLUMNS = ["vertical", "asset_id", "producer", "note", "added"]
HISTORY_COLUMNS = ["period", "vertical", "Producer", *MONEY]


def _overrides_path(store: Path) -> Path:
    return store / "overrides.csv"


def read_overrides(store: Path) -> pd.DataFrame:
    path = _overrides_path(store)
    if not path.exists():
        return pd.DataFrame(columns=OVERRIDE_COLUMNS)
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def overrides_for(store: Path, vertical: str) -> dict[str, str]:
    table = read_overrides(store)
    table = table[table["vertical"] == vertical]
    return dict(zip(table["asset_id"], table["producer"], strict=True))


def add_override(store: Path, vertical: str, asset_id: str, producer: str, note: str = "") -> None:
    """Assign an asset to a producer for this and every later run."""
    store.mkdir(parents=True, exist_ok=True)
    table = read_overrides(store)
    table = table[~((table["vertical"] == vertical) & (table["asset_id"] == asset_id))]
    row = pd.DataFrame([[vertical, asset_id, producer, note, date.today().isoformat()]], columns=OVERRIDE_COLUMNS)
    pd.concat([table, row], ignore_index=True).to_csv(_overrides_path(store), index=False)


def remove_override(store: Path, vertical: str, asset_id: str) -> bool:
    table = read_overrides(store)
    keep = ~((table["vertical"] == vertical) & (table["asset_id"] == asset_id))
    if keep.all():
        return False
    table[keep].to_csv(_overrides_path(store), index=False)
    return True


def _history_path(store: Path) -> Path:
    return store / "history.csv.enc"


def read_history(store: Path) -> pd.DataFrame:
    """Per-period, per-vertical settlement lines of every month run so far (encrypted at rest)."""
    path = _history_path(store)
    if not path.exists():
        return pd.DataFrame(columns=HISTORY_COLUMNS)
    try:
        plain = security.fernet("history").decrypt(path.read_bytes())
    except InvalidToken as exc:
        raise security.KeyError_(
            f"{path} cannot be decrypted with the current {security.MASTER_KEY_ENV}. "
            "It was written with a different key."
        ) from exc
    return pd.read_csv(io.BytesIO(plain), dtype={"period": str})


def save_history(store: Path, period: str, per_vertical: dict[str, pd.DataFrame]) -> None:
    store.mkdir(parents=True, exist_ok=True)
    history = read_history(store)
    history = history[history["period"] != period]
    fresh = [t.assign(period=period, vertical=key)[HISTORY_COLUMNS] for key, t in per_vertical.items()]
    frames = [f for f in (history, *fresh) if not f.empty]
    merged = pd.concat(frames, ignore_index=True) if frames else history
    merged = merged.sort_values(["period", "vertical"], kind="stable")
    token = security.fernet("history").encrypt(merged.to_csv(index=False).encode())
    _history_path(store).write_bytes(token)
