"""Read a month's CMS exports for one vertical into a single tidy frame.

CMS report files are inconsistent: some start with a title row above the real
header, and the per-channel Shorts report has different columns altogether.
Detection is by content, so nobody has to remember which file needs skiprows.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pandas as pd

ASSET_COLUMNS = [
    "Country",
    "Asset Title",
    "Custom ID",
    "Asset ID",
    "Asset Channel ID",
    "Partner Revenue",
    "Asset Labels",
]
SHORTS_REVENUE = "Net Partner Revenue (Post revshare)"
TEXT_COLUMNS = [c for c in ASSET_COLUMNS if c != "Partner Revenue"]
COLUMNS = [*ASSET_COLUMNS, "Report Type", "Source File"]


class IngestError(ValueError):
    pass


def _header_row(path: Path) -> int:
    """Index of the first line that looks like a header (has a revenue column)."""
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for index, row in enumerate(csv.reader(handle)):
            cells = {cell.strip() for cell in row}
            if "Partner Revenue" in cells or SHORTS_REVENUE in cells:
                return index
            if index >= 15:
                break
    raise IngestError(
        f"{path.name}: no 'Partner Revenue' or '{SHORTS_REVENUE}' column "
        "found in the first rows - is this a CMS revenue report?"
    )


def read_report(path: Path) -> pd.DataFrame:
    raw = pd.read_csv(path, skiprows=_header_row(path), encoding="utf-8-sig", low_memory=False)
    raw.columns = [str(c).strip() for c in raw.columns]

    if "Partner Revenue" in raw.columns:
        missing = [c for c in ("Asset ID", "Custom ID") if c not in raw.columns]
        if missing:
            raise IngestError(f"{path.name}: missing column(s) {missing}")
        frame = raw.reindex(columns=ASSET_COLUMNS)
        frame["Report Type"] = "asset"
    else:  # per-channel Shorts report: no assets, attributed later through Channel ID
        if "Channel ID" not in raw.columns:
            raise IngestError(f"{path.name}: Shorts report without a 'Channel ID' column")
        frame = pd.DataFrame(
            {
                "Country": raw.get("Country"),
                "Asset Title": "Shorts - " + raw.get("Channel Display Name", raw["Channel ID"]).astype(str),
                "Asset Channel ID": raw["Channel ID"].astype(str).str.replace(r"\s+", "", regex=True),
                "Partner Revenue": raw[SHORTS_REVENUE],
            }
        ).reindex(columns=ASSET_COLUMNS)
        frame["Report Type"] = "shorts"

    frame["Partner Revenue"] = pd.to_numeric(frame["Partner Revenue"], errors="coerce").fillna(0.0)
    for column in TEXT_COLUMNS:
        frame[column] = frame[column].astype("object").where(frame[column].notna(), "").astype(str).str.strip()
    frame["Source File"] = path.name
    return frame


def load_vertical(folder: Path) -> pd.DataFrame:
    """All revenue rows for a vertical/month. Rows with revenue <= 0 are dropped,
    matching the settlement practice this tool was modelled on."""
    files = sorted(folder.glob("*.csv")) if folder.exists() else []
    if not files:
        return pd.DataFrame(columns=COLUMNS).astype({"Partner Revenue": float})
    frame = pd.concat([read_report(f) for f in files], ignore_index=True)
    return frame[frame["Partner Revenue"] > 0].reset_index(drop=True)
