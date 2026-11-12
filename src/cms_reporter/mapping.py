"""Attribute every revenue row to a producer, or leave it unidentified."""

from __future__ import annotations

import pandas as pd

from .config import Vertical, normalize_id


def _by_custom_id(vertical: Vertical, custom_id: str) -> str | None:
    if not custom_id:
        return None
    for producer in vertical.producers:
        if any(pattern in custom_id for pattern in producer.patterns):
            return producer.name
    return None


def _by_label(label_owner: dict[str, str], labels: str) -> str | None:
    for label in str(labels).split("|"):
        if owner := label_owner.get(label.strip().casefold()):
            return owner
    return None


def resolve(frame: pd.DataFrame, vertical: Vertical, overrides: dict[str, str] | None = None) -> pd.DataFrame:
    """Add 'Producer' (None when unidentified) and 'Matched By' columns.

    Precedence: manual override > asset label > custom-ID pattern > Shorts channel.
    """
    overrides = overrides or {}
    label_owner = {label.casefold(): p.name for p in vertical.producers for label in p.labels}
    channel_owner = {channel: p.name for p in vertical.producers for channel in p.channels}

    out = frame.copy()
    custom = out["Custom ID"].map(normalize_id)
    # Resolve each distinct value once: reports repeat the same asset on thousands of rows.
    custom_hit = custom.map({c: _by_custom_id(vertical, c) for c in custom.unique()})
    label_hit = out["Asset Labels"].map({v: _by_label(label_owner, v) for v in out["Asset Labels"].unique()})
    override_hit = out["Asset ID"].map(overrides)
    channel_hit = out["Asset Channel ID"].map(channel_owner).where(out["Report Type"] == "shorts")

    producer = pd.Series(None, index=out.index, dtype="object")
    matched = pd.Series(None, index=out.index, dtype="object")
    for name, hits in (
        ("override", override_hit),
        ("asset_label", label_hit),
        ("custom_id", custom_hit),
        ("channel", channel_hit),
    ):
        take = producer.isna() & hits.notna()
        producer = producer.where(~take, hits.astype("object"))
        matched = matched.where(~take, name)
    out["Producer"] = producer
    out["Matched By"] = matched
    return out


def unidentified_summary(resolved: pd.DataFrame) -> pd.DataFrame:
    """One line per unidentified asset: what someone needs to look it up in CMS."""
    columns = ["Asset ID", "Custom ID", "Asset Labels", "Asset Title", "Asset Channel ID", "Rows", "Partner Revenue"]
    rows = resolved[resolved["Producer"].isna()]
    if rows.empty:
        return pd.DataFrame(columns=columns)
    grouped = (
        rows.groupby(["Asset ID", "Custom ID", "Asset Labels", "Asset Title"], dropna=False)
        .agg(
            **{
                "Asset Channel ID": ("Asset Channel ID", lambda s: ", ".join(sorted(set(s) - {""}))),
                "Rows": ("Partner Revenue", "size"),
                "Partner Revenue": ("Partner Revenue", "sum"),
            }
        )
        .reset_index()
    )
    return grouped.sort_values("Partner Revenue", ascending=False)[columns].reset_index(drop=True)
