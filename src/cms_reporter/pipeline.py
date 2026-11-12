"""The monthly run: load -> attribute -> settle -> write encrypted outputs."""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from . import security, store
from .analytics import AnalyticsProvider, NoAnalytics
from .config import NetworkConfig, Settings, slugify
from .ingest import ASSET_COLUMNS, load_vertical
from .mapping import resolve, unidentified_summary
from .report_pdf import ReportData, build_report
from .settlement import PeriodInputs, combine, load_period, resolve_fx, settle_vertical, with_local

RAW_COLUMNS = ["Vertical", *ASSET_COLUMNS, "Report Type"]


@dataclass
class VerticalResult:
    key: str
    label: str
    resolved: pd.DataFrame  # every revenue row, with Producer / Matched By
    unidentified: pd.DataFrame  # one line per unidentified asset
    settlement: pd.DataFrame

    @property
    def identified(self) -> pd.DataFrame:
        return self.resolved[self.resolved["Producer"].notna()]

    @property
    def unidentified_revenue(self) -> float:
        return float(self.unidentified["Partner Revenue"].sum())


@dataclass
class PeriodResult:
    period: str
    inputs: PeriodInputs
    verticals: dict[str, VerticalResult]
    combined: pd.DataFrame
    total_usd: float
    fx_rate: float | None
    currency: str

    @property
    def unidentified_revenue(self) -> float:
        return sum(v.unidentified_revenue for v in self.verticals.values())

    def raw_rows(self, producer: str) -> pd.DataFrame:
        frames = [
            v.identified[v.identified["Producer"] == producer].assign(Vertical=v.label) for v in self.verticals.values()
        ]
        return pd.concat(frames, ignore_index=True)[RAW_COLUMNS]

    def statement(self, producer: str) -> pd.DataFrame:
        lines = [
            v.settlement[v.settlement["Producer"] == producer].assign(Vertical=v.label) for v in self.verticals.values()
        ]
        return pd.concat(lines, ignore_index=True).drop(columns="Producer")


def run_period(settings: Settings, cfg: NetworkConfig, period: str) -> PeriodResult:
    root = settings.inbox / period
    if not root.is_dir():
        raise FileNotFoundError(f"No data for {period}: expected a folder at {root}")
    inputs = load_period(root / "period.yaml", period)
    known = set(cfg.producer_names)
    for earning in inputs.other_earnings:
        if earning.vertical not in cfg.verticals or earning.producer not in known:
            raise ValueError(
                f"period.yaml other_earnings: unknown producer/vertical {earning.producer!r}/{earning.vertical!r}"
            )

    verticals: dict[str, VerticalResult] = {}
    total_usd = inputs.other_total()
    for key, vertical in cfg.verticals.items():
        resolved = resolve(load_vertical(root / key), vertical, store.overrides_for(settings.store, key))
        other: dict[str, float] = {}
        for earning in inputs.other_earnings:
            if earning.vertical == key:
                other[earning.producer] = other.get(earning.producer, 0.0) + earning.amount_usd
        pool = inputs.shorts_pool_usd.get(key, 0.0) if vertical.shorts_mode == "pool" else 0.0
        settlement = settle_vertical(resolved[resolved["Producer"].notna()], vertical, cfg, pool, other)
        verticals[key] = VerticalResult(key, vertical.label, resolved, unidentified_summary(resolved), settlement)
        total_usd += float(resolved["Partner Revenue"].sum()) + pool

    fx_rate = resolve_fx(inputs, total_usd)
    return PeriodResult(
        period,
        inputs,
        verticals,
        combine({k: v.settlement for k, v in verticals.items()}),
        total_usd,
        fx_rate,
        cfg.local_currency,
    )


def _workbook(sheets: dict[str, pd.DataFrame]) -> bytes:
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        for name, frame in sheets.items():
            frame.to_excel(writer, sheet_name=name[:31], index=False)
            sheet = writer.sheets[name[:31]]
            for column, title in zip(sheet.columns, frame.columns, strict=True):
                longest = max([len(str(title)), *(len(str(v)) for v in frame[title].head(200))])
                sheet.column_dimensions[column[0].column_letter].width = min(max(longest + 2, 10), 48)
            sheet.freeze_panes = "A2"
    return buffer.getvalue()


def report_data(
    cfg: NetworkConfig,
    result: PeriodResult,
    producer: str,
    rows: pd.DataFrame,
    history: pd.DataFrame,
    analytics: AnalyticsProvider,
) -> ReportData:
    assets = rows[rows["Report Type"] == "asset"].groupby("Asset Title")["Partner Revenue"].sum()
    countries = rows[rows["Country"] != ""].groupby("Country")["Partner Revenue"].sum()
    mine = history[history["Producer"] == producer].groupby("period")["Revenue"].sum().sort_index()
    return ReportData(
        network=cfg.name,
        producer=producer,
        period=result.period,
        currency=result.currency,
        fx_rate=result.fx_rate,
        statement=result.statement(producer),
        top_assets=assets.sort_values(ascending=False).head(10),
        least_assets=assets.sort_values().head(min(10, max(len(assets) - 10, 0))),
        countries=countries.sort_values(ascending=False).head(10),
        history=mine.tail(12),
        audience=analytics.fetch(cfg.channels_of(producer), result.period),
    )


def write_outputs(
    settings: Settings, cfg: NetworkConfig, result: PeriodResult, analytics: AnalyticsProvider | None = None
) -> dict[str, Path]:
    """Write the month's files. Nothing financial is written unencrypted.

    Returns {'internal': workbook path, '<producer name>': client folder, ...}.
    """
    analytics = analytics or NoAnalytics()
    period = result.period
    out = settings.output / period
    (out / "internal").mkdir(parents=True, exist_ok=True)

    store.save_history(settings.store, period, {k: v.settlement for k, v in result.verticals.items()})
    history = store.read_history(settings.store)
    history = history[history["period"] <= period]

    sheets = {"Combined": with_local(result.combined, result.fx_rate, result.currency)}
    for v in result.verticals.values():
        sheets[v.label] = with_local(v.settlement, result.fx_rate, result.currency)
        sheets[f"{v.label} unidentified"] = v.unidentified
    sheets["Run"] = pd.DataFrame(
        {
            "Item": ["Period", "Total revenue (USD)", "Unidentified revenue (USD)", f"FX rate ({result.currency}/USD)"],
            "Value": [period, round(result.total_usd, 6), round(result.unidentified_revenue, 6), result.fx_rate],
        }
    )
    internal = out / "internal" / f"settlement_{period}.xlsx"
    internal.write_bytes(security.encrypt_xlsx(_workbook(sheets), security.password_for(security.INTERNAL)))
    written: dict[str, Path] = {"internal": internal}

    for producer in result.combined["Producer"]:
        slug = slugify(producer)
        folder = out / "clients" / slug
        folder.mkdir(parents=True, exist_ok=True)
        password = security.password_for(slug)
        rows = result.raw_rows(producer)
        stem = f"{slug}_{period}"
        (folder / f"{stem}_raw.xlsx").write_bytes(security.encrypt_xlsx(_workbook({"Revenue": rows}), password))
        security.write_encrypted_zip(
            folder / f"{stem}_raw_csv.zip", {f"{stem}_raw.csv": rows.to_csv(index=False).encode("utf-8-sig")}, password
        )
        build_report(
            folder / f"{stem}_report.pdf", report_data(cfg, result, producer, rows, history, analytics), password
        )
        written[producer] = folder
    return written
