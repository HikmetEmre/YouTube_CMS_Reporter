"""Synthetic CMS exports for demos and tests. No real client data is involved:
producers come from the (fictional) network config and every ID, title and
amount is generated from a seeded random stream, so runs are reproducible.

The files deliberately reproduce the awkward parts of real exports: title rows
above the header, far more columns than the pipeline needs, blank or untidy
Custom IDs, a per-channel Shorts report with different columns, and a few
assets nobody has labelled yet.
"""

from __future__ import annotations

import zlib
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from .config import NetworkConfig, Producer, Settings, Vertical, slugify
from .ingest import SHORTS_REVENUE

COUNTRIES = ["TR", "DE", "US", "NL", "FR", "GB", "AZ", "AT", "BE", "CH", "SE", "CA", "AU", "SA"]
WEIGHTS = np.array([42, 13, 11, 5, 5, 4, 4, 3, 3, 2, 2, 2, 2, 2], dtype=float)
WEIGHTS /= WEIGHTS.sum()

ADJECTIVES = [
    "Silent",
    "Golden",
    "Distant",
    "Paper",
    "Midnight",
    "Hollow",
    "Bright",
    "Winter",
    "Crimson",
    "Quiet",
    "Electric",
    "Wandering",
    "Salt",
    "Velvet",
    "Broken",
    "Northern",
    "Second",
    "Slow",
]
NOUNS = [
    "Harbor",
    "Lanterns",
    "Orchard",
    "Signal",
    "Rivers",
    "Compass",
    "Echoes",
    "Garden",
    "Station",
    "Letters",
    "Shoreline",
    "Machines",
    "Sparrow",
    "Bridges",
    "Weather",
    "Atlas",
    "Windows",
    "Tides",
]
ASSET_TYPES = {
    "music": ["Sound Recording", "Music Video", "Art Track"],
    "non_music": ["Web", "Movie", "Television Episode"],
}
UNKNOWN_CUSTOM_IDS = ["", "legacy_upload_0412", "TMP Migrate 77"]
UNKNOWN_SHORTS_CHANNEL = "UCDEMO000000000000000999"


def _rng(*parts: object) -> np.random.Generator:
    return np.random.default_rng(zlib.crc32("/".join(map(str, parts)).encode()))


def recent_periods(end: str, months: int) -> list[str]:
    last = pd.Period(end, freq="M")
    return [str(last - i) for i in range(months - 1, -1, -1)]


def _title(rng: np.random.Generator) -> str:
    return f"{rng.choice(ADJECTIVES)} {rng.choice(NOUNS)}"


def _catalogue(vertical: Vertical, producer: Producer) -> pd.DataFrame:
    """A producer's assets. Stable from month to month, like a real catalogue."""
    rng = _rng("catalogue", vertical.key, producer.name)
    rows = []
    for index in range(int(rng.integers(18, 42))):
        title = f"{_title(rng)} {index + 1}" if rng.random() < 0.3 else _title(rng)
        custom_id = f"{rng.choice(producer.patterns)}_{slugify(title).replace('-', '')}"
        label = str(rng.choice(producer.labels))
        style = rng.random()
        if style < 0.15:
            custom_id = ""  # only the label identifies it
        elif style < 0.25:
            custom_id = custom_id.upper().replace("_", " ")  # untidy but still matchable
            label = "" if rng.random() < 0.5 else label
        elif style < 0.60:
            label = ""  # only the custom ID identifies it
        rows.append(
            {
                "Asset ID": f"A{rng.integers(10**14, 10**15)}",
                "Asset Title": title,
                "Custom ID": custom_id,
                "Asset Labels": label,
                "Asset Channel ID": str(rng.choice(producer.channels)) if producer.channels else "",
                "Asset Type": str(rng.choice(ASSET_TYPES.get(vertical.key, ["Web"]))),
                "weight": float(rng.lognormal(0.0, 1.1)),
            }
        )
    return pd.DataFrame(rows)


def _unknown_catalogue(vertical: Vertical) -> pd.DataFrame:
    rng = _rng("unknown", vertical.key)
    return pd.DataFrame(
        [
            {
                "Asset ID": f"A{rng.integers(10**14, 10**15)}",
                "Asset Title": _title(rng),
                "Custom ID": custom_id,
                "Asset Labels": "Unsorted Imports" if custom_id == "" else "",
                "Asset Channel ID": f"UCDEMO0000000000000009{index:02d}",
                "Asset Type": ASSET_TYPES.get(vertical.key, ["Web"])[0],
                "weight": 1.0,
            }
            for index, custom_id in enumerate(UNKNOWN_CUSTOM_IDS)
        ]
    )


def _rows(catalogue: pd.DataFrame, budget: float, rng: np.random.Generator) -> pd.DataFrame:
    """Spread a monthly budget over assets x countries."""
    shares = catalogue["weight"].to_numpy() * rng.uniform(0.6, 1.4, len(catalogue))
    shares /= shares.sum()
    out = []
    for asset, share in zip(catalogue.to_dict("records"), shares, strict=True):
        countries = rng.choice(COUNTRIES, size=int(rng.integers(4, 11)), replace=False, p=WEIGHTS)
        split = rng.dirichlet(np.array([WEIGHTS[COUNTRIES.index(c)] for c in countries]) * 20)
        for country, part in zip(countries, split, strict=True):
            revenue = round(budget * share * part, 6)
            out.append({**asset, "Country": country, "Partner Revenue": revenue})
    return pd.DataFrame(out).drop(columns="weight")


def _dress(rows: pd.DataFrame, rng: np.random.Generator, adjustment: str = "None") -> pd.DataFrame:
    """Add the extra columns a real export carries, in export-like order."""
    n = len(rows)
    auction = (rows["Partner Revenue"] * rng.uniform(0.55, 0.9, n)).round(6)
    return pd.DataFrame(
        {
            "Adjustment Type": adjustment,
            "Country": rows["Country"],
            "Asset ID": rows["Asset ID"],
            "Asset Labels": rows["Asset Labels"],
            "Asset Channel ID": rows["Asset Channel ID"],
            "Custom ID": rows["Custom ID"],
            "Asset Title": rows["Asset Title"],
            "Asset Type": rows["Asset Type"],
            "Content Type": rng.choice(["Partner-provided", "UGC"], n, p=[0.7, 0.3]),
            "Policy": "Monetize in all countries",
            "Owned Views": (rows["Partner Revenue"].abs() * rng.uniform(300, 900, n)).astype(int),
            "YouTube Revenue Split : Auction": (auction * 0.45 / 0.55).round(6),
            "YouTube Revenue Split : Reserved": 0.0,
            "Partner Revenue : Auction": auction,
            "Partner Revenue : Reserved": (rows["Partner Revenue"] - auction).round(6),
            "Partner Revenue": rows["Partner Revenue"],
        }
    )


def _write(frame: pd.DataFrame, path: Path, title_row: str | None = None) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        if title_row:
            handle.write(title_row + "\n")
        frame.to_csv(handle, index=False)


def generate_period(settings: Settings, cfg: NetworkConfig, period: str) -> Path:
    """Write one month of synthetic exports plus its period.yaml. Returns the month folder."""
    root = settings.inbox / period
    month = pd.Period(period, freq="M")
    total_usd = 0.0
    first_client: tuple[str, str] | None = None

    for key, vertical in cfg.verticals.items():
        folder = root / key
        folder.mkdir(parents=True, exist_ok=True)
        for stale in folder.glob("*.csv"):
            stale.unlink()
        rng = _rng("period", period, key)
        frames = []
        for rank, producer in enumerate(vertical.producers):
            base = _rng("scale", key, producer.name).uniform(900, 26000) / (1 + 0.35 * rank)
            base /= 4 if key != "music" else 1
            season = 1 + 0.18 * np.sin((month.month + rank) / 12 * 2 * np.pi)
            growth = 1 + 0.015 * (month.year * 12 + month.month - 24300)
            frames.append(_rows(_catalogue(vertical, producer), base * season * growth * rng.uniform(0.9, 1.1), rng))
            first_client = first_client or (producer.name, key)
        frames.append(_rows(_unknown_catalogue(vertical), rng.uniform(40, 160), rng))
        rows = pd.concat(frames, ignore_index=True).sample(frac=1, random_state=int(rng.integers(1 << 31)))

        half = len(rows) // 2
        _write(_dress(rows.iloc[:half], rng), folder / f"{key}_ads_assets_1.csv")
        _write(
            _dress(rows.iloc[half:], rng),
            folder / f"{key}_ads_assets_2.csv",
            title_row=f"Asset Summary Report,{period}",
        )
        total_usd += rows["Partner Revenue"].sum()

        if key == "music":  # subscription ("audio tier") revenue for part of the catalogue
            tier = rows.sample(frac=0.25, random_state=7).copy()
            tier["Partner Revenue"] = (tier["Partner Revenue"] * 0.3).round(6)
            _write(_dress(tier, rng), folder / f"{key}_audio_tier.csv")
            total_usd += tier["Partner Revenue"].sum()

        adjustments = rows.sample(n=12, random_state=11).copy()
        adjustments["Partner Revenue"] = np.round(np.resize([0.42, 0.0, -0.31, 1.18], 12), 6)
        _write(
            _dress(adjustments, rng, "Monthly Adjustment"),
            folder / f"{key}_adjustments.csv",
            title_row=f"Adjustment Report,{period}",
        )
        total_usd += adjustments.loc[adjustments["Partner Revenue"] > 0, "Partner Revenue"].sum()

        if vertical.shorts_mode == "per_channel":
            channels = [(c, p.name) for p in vertical.producers for c in p.channels]
            channels.append((UNKNOWN_SHORTS_CHANNEL, "Unclaimed Channel"))
            shorts = pd.DataFrame(
                {
                    # real exports sometimes carry stray whitespace in the ID
                    "Channel ID": [f" {c} " if i % 3 == 0 else c for i, (c, _) in enumerate(channels)],
                    "Channel Display Name": [f"{name} {i + 1}" for i, (_, name) in enumerate(channels)],
                    SHORTS_REVENUE: rng.uniform(1, 60, len(channels)).round(6),
                }
            )
            _write(shorts, folder / f"{key}_shorts_channels.csv")
            total_usd += shorts[SHORTS_REVENUE].sum()

    rng = _rng("period-inputs", period)
    pools = {k: round(float(rng.uniform(250, 700)), 2) for k, v in cfg.verticals.items() if v.shorts_mode == "pool"}
    other = []
    if first_client:
        other.append(
            {
                "producer": first_client[0],
                "vertical": first_client[1],
                "amount_usd": round(float(rng.uniform(5, 40)), 2),
                "note": "Channel store (e-commerce)",
            }
        )
    total_usd += sum(pools.values()) + sum(e["amount_usd"] for e in other)
    rate = 38.0 + 0.55 * (month.year * 12 + month.month - 24310) + float(rng.uniform(-0.2, 0.2))
    period_file = {
        "period": period,
        "fx": {"currency": cfg.local_currency, "payout_local": round(float(total_usd * rate), 2)},
        "shorts_pool_usd": pools,
        "other_earnings": other,
    }
    (root / "period.yaml").write_text(
        "# Month-specific figures that are not in the CMS exports. Synthetic demo values.\n"
        "# fx: give either `rate` (local currency per USD) or `payout_local` (the amount\n"
        "# actually received; the effective rate is then derived from it).\n"
        + yaml.safe_dump(period_file, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return root
