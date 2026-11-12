"""Turn attributed revenue into what each producer is owed.

Per producer and vertical:

    net        = revenue + shorts share + other earnings - withholding tax
    commission = net x the producer's rate in that vertical
    payment    = net - commission

All amounts are USD; a single monthly FX rate converts the result to the
network's local currency.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
import yaml

from .config import NetworkConfig, Vertical

MONEY = [
    "Revenue",
    "Shorts Share",
    "Other Earnings",
    "Withholding Tax",
    "Net Revenue",
    "Network Commission",
    "Partner Payment",
]
COLUMNS = [
    "Producer",
    "Revenue",
    "Shorts Share",
    "Other Earnings",
    "Withholding Tax",
    "Net Revenue",
    "Commission Rate",
    "Network Commission",
    "Partner Payment",
]


class PeriodError(ValueError):
    pass


@dataclass
class OtherEarning:
    producer: str
    vertical: str
    amount_usd: float
    note: str = ""


@dataclass
class PeriodInputs:
    """Month-specific figures that are not in the CMS exports (period.yaml)."""

    period: str
    fx_rate: float | None = None  # local currency per USD, if stated directly
    payout_local: float | None = None  # or: what was actually received, to derive it
    shorts_pool_usd: dict[str, float] = field(default_factory=dict)
    other_earnings: list[OtherEarning] = field(default_factory=list)

    def other_total(self) -> float:
        return sum(e.amount_usd for e in self.other_earnings)


def load_period(path: Path, period: str) -> PeriodInputs:
    if not path.exists():
        return PeriodInputs(period)
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    fx = raw.get("fx") or {}
    try:
        return PeriodInputs(
            period=period,
            fx_rate=float(fx["rate"]) if fx.get("rate") is not None else None,
            payout_local=float(fx["payout_local"]) if fx.get("payout_local") is not None else None,
            shorts_pool_usd={k: float(v) for k, v in (raw.get("shorts_pool_usd") or {}).items()},
            other_earnings=[
                OtherEarning(e["producer"], e["vertical"], float(e["amount_usd"]), str(e.get("note", "")))
                for e in raw.get("other_earnings") or []
            ],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise PeriodError(f"{path}: {exc!r}") from exc


def settle_vertical(
    identified: pd.DataFrame,
    vertical: Vertical,
    cfg: NetworkConfig,
    shorts_pool: float = 0.0,
    other: dict[str, float] | None = None,
) -> pd.DataFrame:
    """`identified` holds only rows with a Producer. Returns one line per producer."""
    other = other or {}
    revenue = identified.groupby("Producer")["Partner Revenue"].sum()
    names = [n for n in vertical.producer_names if n in revenue.index or other.get(n)]
    table = pd.DataFrame({"Producer": names})
    table["Revenue"] = table["Producer"].map(revenue).fillna(0.0)

    total = table["Revenue"].sum()
    pool = shorts_pool if vertical.shorts_mode == "pool" else 0.0
    table["Shorts Share"] = table["Revenue"] / total * pool if total else 0.0
    table["Other Earnings"] = table["Producer"].map(other).fillna(0.0)

    tax = pd.Series(0.0, index=revenue.index)
    country = identified["Country"].str.upper()
    for code, rate in cfg.withholding.items():
        taxed = identified[country == code].groupby("Producer")["Partner Revenue"].sum() * rate
        tax = tax.add(taxed, fill_value=0.0)
    table["Withholding Tax"] = table["Producer"].map(tax).fillna(0.0)

    table["Net Revenue"] = table["Revenue"] + table["Shorts Share"] + table["Other Earnings"] - table["Withholding Tax"]
    table["Commission Rate"] = table["Producer"].map({p.name: p.commission for p in vertical.producers})
    table["Network Commission"] = table["Net Revenue"] * table["Commission Rate"]
    table["Partner Payment"] = table["Net Revenue"] - table["Network Commission"]
    return table.sort_values("Revenue", ascending=False)[COLUMNS].reset_index(drop=True)


def combine(per_vertical: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """One line per client across verticals (a client can be in several)."""
    frames = [t for t in per_vertical.values() if not t.empty]
    if not frames:
        return pd.DataFrame(columns=["Producer", *MONEY])
    total = pd.concat(frames).groupby("Producer", sort=False)[MONEY].sum().reset_index()
    return total.sort_values("Revenue", ascending=False).reset_index(drop=True)


def resolve_fx(inputs: PeriodInputs, total_usd: float) -> float | None:
    """Stated rate, else the effective rate of the payout actually received."""
    if inputs.fx_rate:
        return inputs.fx_rate
    if inputs.payout_local and total_usd > 0:
        return inputs.payout_local / total_usd
    return None


def with_local(table: pd.DataFrame, rate: float | None, currency: str) -> pd.DataFrame:
    out = table.copy()
    if rate:
        out[f"Network Commission ({currency})"] = out["Network Commission"] * rate
        out[f"Partner Payment ({currency})"] = out["Partner Payment"] * rate
    return out
