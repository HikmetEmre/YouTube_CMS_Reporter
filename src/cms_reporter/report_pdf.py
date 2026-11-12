"""The monthly client report: a password-protected PDF statement with charts."""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

import matplotlib
import pandas as pd
from pypdf import PdfReader, PdfWriter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from . import charts
from .analytics import AudienceAnalytics

INK, INK_2, MUTED, RULE = (colors.HexColor(c) for c in ("#0b0b0b", "#52514e", "#898781", "#e1e0d9"))
TILE = colors.HexColor("#f4f4f1")
CONTENT_WIDTH = A4[0] - 36 * mm

_fonts = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
pdfmetrics.registerFont(TTFont("Body", str(_fonts / "DejaVuSans.ttf")))
pdfmetrics.registerFont(TTFont("Body-Bold", str(_fonts / "DejaVuSans-Bold.ttf")))

H1 = ParagraphStyle("h1", fontName="Body-Bold", fontSize=20, leading=24, textColor=INK)
SUB = ParagraphStyle("sub", fontName="Body", fontSize=10, leading=14, textColor=INK_2)
H2 = ParagraphStyle("h2", fontName="Body-Bold", fontSize=11.5, leading=15, textColor=INK, spaceBefore=14, spaceAfter=2)
NOTE = ParagraphStyle("note", fontName="Body", fontSize=8, leading=11, textColor=MUTED)
BODY = ParagraphStyle("body", fontName="Body", fontSize=9, leading=13, textColor=INK_2)
TILE_LABEL = ParagraphStyle("tl", fontName="Body", fontSize=8, leading=10, textColor=INK_2)
TILE_VALUE = ParagraphStyle("tv", fontName="Body-Bold", fontSize=14, leading=18, textColor=INK)
TILE_SUB = ParagraphStyle("ts", fontName="Body", fontSize=8, leading=10, textColor=MUTED)


@dataclass
class ReportData:
    network: str
    producer: str
    period: str
    currency: str
    fx_rate: float | None
    statement: pd.DataFrame  # settlement lines with a leading 'Vertical' column
    top_assets: pd.Series  # title -> revenue, descending
    least_assets: pd.Series  # title -> revenue, ascending
    countries: pd.Series  # country -> revenue, descending
    history: pd.Series  # 'YYYY-MM' -> revenue, including this period
    audience: AudienceAnalytics | None = None


def usd(value: float) -> str:
    return f"${value:,.2f}"


def _image(png: bytes) -> Image:
    image = Image(io.BytesIO(png))
    scale = CONTENT_WIDTH / image.imageWidth
    image.drawWidth, image.drawHeight = CONTENT_WIDTH, image.imageHeight * scale
    return image


def _section(title: str, caption: str, png: bytes, extra: str | None = None) -> KeepTogether:
    parts = [Paragraph(title, H2), Paragraph(caption, NOTE), Spacer(1, 4), _image(png)]
    if extra:
        parts.append(Paragraph(extra, NOTE))
    return KeepTogether(parts)


def _tiles(data: ReportData) -> Table:
    totals = data.statement[
        ["Revenue", "Shorts Share", "Other Earnings", "Withholding Tax", "Network Commission", "Partner Payment"]
    ].sum()
    gross = totals["Revenue"] + totals["Shorts Share"] + totals["Other Earnings"]

    def local(value: float) -> str:
        return f"{value * data.fx_rate:,.2f} {data.currency}" if data.fx_rate else ""

    tiles = [
        ("Gross earnings", gross),
        ("Withholding tax", totals["Withholding Tax"]),
        ("Network commission", totals["Network Commission"]),
        ("Your payment", totals["Partner Payment"]),
    ]
    cells = [
        [Paragraph(label, TILE_LABEL), Paragraph(usd(value), TILE_VALUE), Paragraph(local(value), TILE_SUB)]
        for label, value in tiles
    ]
    gap = 6
    width = (CONTENT_WIDTH - gap * 3) / 4
    row = []
    for index, cell in enumerate(cells):
        row.append(Table([[c] for c in cell], colWidths=[width - 16]))
        if index < 3:
            row.append("")
    table = Table([row], colWidths=[width, gap, width, gap, width, gap, width])
    style = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]
    style += [("BACKGROUND", (i, 0), (i, 0), TILE) for i in (0, 2, 4, 6)]
    style += [("LEFTPADDING", (i, 0), (i, 0), 0) for i in (1, 3, 5)] + [
        ("RIGHTPADDING", (i, 0), (i, 0), 0) for i in (1, 3, 5)
    ]
    table.setStyle(TableStyle(style))
    return table


def _statement(data: ReportData) -> Table:
    head = ["Vertical", "Revenue", "Shorts", "Other", "Withholding", "Net", "Rate", "Commission", "Payment"]
    money_cols = ["Revenue", "Shorts Share", "Other Earnings", "Withholding Tax", "Net Revenue"]
    rows = [head]
    for line in data.statement.to_dict("records"):
        rows.append(
            [
                line["Vertical"],
                *(usd(line[c]) for c in money_cols),
                f"{line['Commission Rate']:.0%}",
                usd(line["Network Commission"]),
                usd(line["Partner Payment"]),
            ]
        )
    total = data.statement.sum(numeric_only=True)
    rows.append(
        [
            "Total",
            *(usd(total[c]) for c in money_cols),
            "",
            usd(total["Network Commission"]),
            usd(total["Partner Payment"]),
        ]
    )
    first = 58
    table = Table(
        rows, colWidths=[first, *[(CONTENT_WIDTH - first - 30) / 7] * 5, 30, *[(CONTENT_WIDTH - first - 30) / 7] * 2]
    )
    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), "Body"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("TEXTCOLOR", (0, 0), (-1, 0), MUTED),
                ("TEXTCOLOR", (0, 1), (-1, -1), INK_2),
                ("FONTNAME", (0, -1), (-1, -1), "Body-Bold"),
                ("TEXTCOLOR", (0, -1), (-1, -1), INK),
                ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
                ("LINEBELOW", (0, 0), (-1, 0), 0.6, RULE),
                ("LINEABOVE", (0, -1), (-1, -1), 0.6, RULE),
                ("LEFTPADDING", (0, 0), (-1, -1), 2),
                ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return table


def render_report(data: ReportData, password: str | None = None) -> bytes:
    month = pd.Period(data.period, freq="M").strftime("%B %Y")

    def footer(canvas, doc):
        canvas.setFont("Body", 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(18 * mm, 10 * mm, f"{data.network} · {data.producer} · {month} · Confidential")
        canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Page {doc.page}")

    story = [
        Paragraph(data.producer, H1),
        Paragraph(f"Royalty statement · {month} · {data.network}", SUB),
        Spacer(1, 12),
        _tiles(data),
        Spacer(1, 4),
    ]
    rate = f"Converted at {data.fx_rate:,.4f} {data.currency} per USD. " if data.fx_rate else ""
    story += [
        Paragraph(f"{rate}Payment = earnings less withholding tax and network commission.", NOTE),
        Paragraph("Statement", H2),
        _statement(data),
    ]

    if len(data.history) >= 2:
        story.append(
            _section(
                "Revenue trend", "Revenue before Shorts and deductions, by month (USD)", charts.trend(data.history)
            )
        )
    if not data.top_assets.empty:
        story.append(_section("Top earning assets", f"Revenue in {month} (USD)", charts.ranked_bars(data.top_assets)))
    if not data.countries.empty:
        story.append(
            _section("Revenue by country", f"Top countries in {month} (USD)", charts.ranked_bars(data.countries))
        )
    if len(data.least_assets) >= 3:
        story.append(
            _section(
                "Lowest earning assets",
                f"Assets that earned least in {month} (USD)",
                charts.ranked_bars(data.least_assets),
            )
        )
    if data.audience is not None and not data.audience.demographics.empty:
        audience = data.audience
        by_gender = audience.demographics.groupby("gender")["viewer_pct"].sum()
        split = " · ".join(f"{g} {by_gender.get(g, 0):.0f}%" for g in ("Female", "Male", "Other"))
        summary = (
            f"{audience.views:,} views · {audience.watch_hours:,.0f} watch hours · "
            f"{audience.subscribers_gained:,} new subscribers · {split}"
        )
        if audience.synthetic:
            summary += " · Demo data, not real channel analytics."
        story.append(
            _section(
                "Audience by age and gender",
                "Share of viewers across your channels (%)",
                charts.demographics(audience.demographics),
                extra=summary,
            )
        )

    plain = io.BytesIO()
    SimpleDocTemplate(
        plain,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=18 * mm,
        title=f"{data.producer} - {month}",
        author=data.network,
    ).build(story, onFirstPage=footer, onLaterPages=footer)
    if not password:
        return plain.getvalue()
    writer = PdfWriter(clone_from=PdfReader(plain))
    writer.encrypt(user_password=password, algorithm="AES-256")
    locked = io.BytesIO()
    writer.write(locked)
    return locked.getvalue()


def build_report(path: Path, data: ReportData, password: str | None = None) -> None:
    path.write_bytes(render_report(data, password))
