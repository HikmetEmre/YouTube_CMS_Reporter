"""Static charts for the PDF reports. Each function returns PNG bytes.

One visual system throughout: a quiet surface, hairline grid, thin bars with a
rounded data end, 2px lines, and text in ink colours rather than series colours.
"""

from __future__ import annotations

import io

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.patches import PathPatch  # noqa: E402
from matplotlib.path import Path as MplPath  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402

from .analytics import AGE_GROUPS, GENDERS  # noqa: E402

SURFACE, INK, INK_2, MUTED = "#fcfcfb", "#0b0b0b", "#52514e", "#898781"
GRID, AXIS = "#e1e0d9", "#c3c2b7"
SERIES = ["#2a78d6", "#eb6834", "#1baf7a"]  # fixed categorical order
DPI = 200
PX = DPI / 96  # one CSS pixel in device pixels
WIDTH_IN = 6.9

plt.rcParams.update(
    {
        "font.family": "DejaVu Sans",
        "font.size": 8,
        "text.color": INK_2,
        "axes.edgecolor": AXIS,
        "axes.labelcolor": MUTED,
        "axes.linewidth": 0.6,
        "xtick.color": MUTED,
        "ytick.color": MUTED,
        "xtick.labelsize": 7.5,
        "ytick.labelsize": 7.5,
        "figure.facecolor": SURFACE,
        "axes.facecolor": SURFACE,
        "savefig.facecolor": SURFACE,
    }
)


def money(value: float) -> str:
    if abs(value) >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"
    if abs(value) >= 10_000:
        return f"${value / 1000:.1f}K"
    return f"${value:,.0f}" if abs(value) >= 100 else f"${value:,.2f}"


def _figure(height_in: float, left: float, right: float = 0.97, top: float = 0.95, bottom: float = 0.12):
    fig, ax = plt.subplots(figsize=(WIDTH_IN, height_in), dpi=DPI)
    fig.subplots_adjust(left=left, right=right, top=top, bottom=bottom)
    for side in ("top", "right", "left", "bottom"):
        ax.spines[side].set_visible(False)
    ax.tick_params(length=0)
    return fig, ax


def _png(fig) -> bytes:
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=DPI)
    plt.close(fig)
    return buffer.getvalue()


def _bar(ax, start: tuple[float, float], end: tuple[float, float], color: str, horizontal: bool) -> None:
    """A bar from `start` to `end` (data coords, opposite corners) whose data end is
    rounded 4px and whose baseline end is square. Drawn in pixels so the corner
    radius is the same on every chart; call after the axis limits are final."""
    (x0, y0), (x1, y1) = ax.transData.transform([start, end])
    if horizontal:
        r = min(4 * PX, abs(x1 - x0), abs(y1 - y0) / 2)
        verts = [
            (x0, y0),
            (x1 - r, y0),
            (x1, y0),
            (x1, y0 + r),
            (x1, y1 - r),
            (x1, y1),
            (x1 - r, y1),
            (x0, y1),
            (x0, y0),
        ]
    else:
        r = min(4 * PX, abs(y1 - y0), abs(x1 - x0) / 2)
        verts = [
            (x0, y0),
            (x0, y1 - r),
            (x0, y1),
            (x0 + r, y1),
            (x1 - r, y1),
            (x1, y1),
            (x1, y1 - r),
            (x1, y0),
            (x0, y0),
        ]
    codes = [
        MplPath.MOVETO,
        MplPath.LINETO,
        MplPath.CURVE3,
        MplPath.CURVE3,
        MplPath.LINETO,
        MplPath.CURVE3,
        MplPath.CURVE3,
        MplPath.LINETO,
        MplPath.CLOSEPOLY,
    ]
    ax.add_artist(PathPatch(MplPath(verts, codes), transform=None, facecolor=color, edgecolor="none"))


def _shorten(text: str, limit: int = 30) -> str:
    text = str(text)
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def ranked_bars(values: pd.Series, fmt=money) -> bytes:
    """Horizontal bars in the order given (first on top), value at each tip."""
    n = len(values)
    fig, ax = _figure(height_in=0.42 + 0.27 * n, left=0.31, right=0.9, top=0.98, bottom=0.04)
    peak = float(values.max()) or 1.0
    ax.set_xlim(0, peak)
    ax.set_ylim(n - 0.5, -0.5)
    ax.set_xticks([])
    ax.set_yticks(range(n), [_shorten(label) for label in values.index], color=INK_2, fontsize=8)
    ax.axvline(0, color=AXIS, linewidth=0.8)
    thickness = 0.62  # of a 0.27in band: about 16 CSS px
    for i, value in enumerate(values):
        _bar(ax, (0, i + thickness / 2), (max(float(value), 0), i - thickness / 2), SERIES[0], horizontal=True)
        ax.annotate(
            fmt(value),
            (value, i),
            xytext=(5, 0),
            textcoords="offset points",
            va="center",
            ha="left",
            color=INK,
            fontsize=8,
        )
    return _png(fig)


def demographics(table: pd.DataFrame) -> bytes:
    """Grouped columns: share of viewers by age group, one series per gender."""
    pivot = (
        table.pivot_table(index="age_group", columns="gender", values="viewer_pct", aggfunc="sum")
        .reindex(index=AGE_GROUPS, columns=GENDERS)
        .fillna(0.0)
    )
    fig, ax = _figure(height_in=2.7, left=0.07, top=0.86, bottom=0.12)
    peak = float(pivot.to_numpy().max()) or 1.0
    ax.set_xlim(-0.5, len(AGE_GROUPS) - 0.5)
    ax.set_ylim(0, peak * 1.12)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.0f}%"))
    ax.locator_params(axis="y", nbins=5)
    ax.grid(axis="y", color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)
    ax.axhline(0, color=AXIS, linewidth=0.8)
    ax.set_xticks(range(len(AGE_GROUPS)), AGE_GROUPS, color=INK_2, fontsize=8)

    width, gap = 0.2, 0.025  # the gap is surface colour showing between neighbours
    for j, gender in enumerate(GENDERS):
        offset = (j - (len(GENDERS) - 1) / 2) * (width + gap)
        for i, value in enumerate(pivot[gender]):
            if value > 0:
                _bar(
                    ax, (i + offset - width / 2, 0), (i + offset + width / 2, float(value)), SERIES[j], horizontal=False
                )
    top_age, top_gender = pivot.stack().idxmax()
    x = AGE_GROUPS.index(top_age) + (GENDERS.index(top_gender) - 1) * (width + gap)
    ax.annotate(
        f"{pivot.loc[top_age, top_gender]:.1f}%",
        (x, pivot.loc[top_age, top_gender]),
        xytext=(0, 3),
        textcoords="offset points",
        ha="center",
        va="bottom",
        color=INK,
        fontsize=8,
    )
    handles = [plt.Rectangle((0, 0), 1, 1, color=SERIES[j]) for j in range(len(GENDERS))]
    fig.legend(
        handles,
        GENDERS,
        loc="upper left",
        bbox_to_anchor=(0.06, 1.0),
        ncols=3,
        frameon=False,
        handlelength=0.9,
        handleheight=0.9,
        columnspacing=1.6,
        fontsize=8,
        labelcolor=INK_2,
    )
    return _png(fig)


def trend(values: pd.Series) -> bytes:
    """Revenue by month (index: 'YYYY-MM'), latest month labelled."""
    labels = [pd.Period(p, freq="M").strftime("%b %Y") for p in values.index]
    fig, ax = _figure(height_in=2.4, left=0.09, right=0.9, top=0.93, bottom=0.13)
    xs = list(range(len(values)))
    ax.plot(
        xs,
        values.to_numpy(),
        color=SERIES[0],
        linewidth=2,
        solid_joinstyle="round",
        solid_capstyle="round",
        marker="o",
        markersize=7,
        markeredgecolor=SURFACE,
        markeredgewidth=1.6,
        clip_on=False,
        zorder=3,
    )
    ax.set_xlim(-0.3, len(values) - 0.7)
    ax.set_ylim(0, float(values.max()) * 1.18 or 1)
    compact = float(values.max()) >= 10_000  # one tick style per axis
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"${v / 1000:,.0f}K" if compact and v else f"${v:,.0f}"))
    ax.locator_params(axis="y", nbins=5)
    ax.grid(axis="y", color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)
    ax.axhline(0, color=AXIS, linewidth=0.8)
    ax.set_xticks(xs, labels, color=INK_2, fontsize=8)
    ax.annotate(
        money(float(values.iloc[-1])),
        (xs[-1], values.iloc[-1]),
        xytext=(8, 0),
        textcoords="offset points",
        va="center",
        ha="left",
        color=INK,
        fontsize=8.5,
        fontweight="bold",
    )
    return _png(fig)
