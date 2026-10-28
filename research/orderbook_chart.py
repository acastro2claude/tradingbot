"""Bookmap-style picture of the rebuilt book around one trade, to check the rebuild by eye.

Shading = resting contracts at each price each second (darker = more).
Dots = traded volume per second and price (size = contracts), buys and sells.
The grey line is the last trade price; the black marker is the entry.

Run from the repo root:
    python -m research.orderbook_chart 2024-03-25 0      (day, trade number that day)
"""

from __future__ import annotations

import sys

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt                      # noqa: E402
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402

from research.journal_study import OUT              # noqa: E402
from research.orderbook import Book, build_book, load_mbo  # noqa: E402
from research.orderbook_study import LOOKBACK_S, MBO_DIR   # noqa: E402

SURFACE, INK, MUTED = "#fcfcfb", "#0b0b0b", "#52514e"
BUY, SELL = "#1baf7a", "#eb6834"
RAMP = LinearSegmentedColormap.from_list(
    "depth", [SURFACE, "#cde2fb", "#86b6ef", "#3987e5", "#1c5cab", "#0d366b"])
CHARTS = OUT / "orderbook_charts"


def draw(book: Book, t_in: float, d: int, entry: float, t_out: float, exit_: float, points: float,
         title: str, path, before_s: int = 360, after_s: int = 240, half_points: float = 8.0) -> None:
    r0 = max(book.row(t_in) - before_s, 0)
    r1 = min(book.row(max(t_out, t_in)) + after_s // 2, len(book.bid) - 1)
    r1 = max(r1, min(book.row(t_in) + after_s, len(book.bid) - 1))
    e = int(round(entry / 0.25))
    p0, p1 = e - int(half_points * 4), e + int(half_points * 4)
    depth = book.bid[r0:r1, p0 - book.lo:p1 - book.lo + 1] + book.ask[r0:r1, p0 - book.lo:p1 - book.lo + 1]
    vmax = np.percentile(depth[depth > 0], 98) if (depth > 0).any() else 1

    fig, ax = plt.subplots(figsize=(11, 6), dpi=110, facecolor=SURFACE)
    ax.set_facecolor(SURFACE)
    x0 = book.t0 + r0
    extent = [0, r1 - r0, (p0 - 0.5) * 0.25, (p1 + 0.5) * 0.25]
    im = ax.imshow(depth.T, origin="lower", aspect="auto", cmap=RAMP, vmin=0, vmax=vmax,
                   extent=extent, interpolation="nearest")

    tr = book.trades[(book.trades["t"] >= x0) & (book.trades["t"] < book.t0 + r1)].copy()
    tr = tr[(tr["price"] >= p0) & (tr["price"] <= p1)]
    tr["sec"] = np.floor(tr["t"] - x0)
    last = tr.sort_values("t")
    ax.plot(last["t"] - x0, last["price"] * 0.25, color=MUTED, lw=1, drawstyle="steps-post", zorder=2)
    agg = tr.groupby(["sec", "price", "side"], as_index=False)["size"].sum()
    big = agg[agg["size"] >= 20]
    for side, colour, marker, label in (("B", BUY, "^", "buy aggressors (20+ per second)"),
                                        ("A", SELL, "v", "sell aggressors (20+ per second)")):
        g = big[big["side"] == side]
        ax.scatter(g["sec"] + 0.5, g["price"] * 0.25, s=np.clip(g["size"], 20, 600) / 2, c=colour,
                   marker=marker, edgecolors=INK, linewidths=0.4, alpha=0.9, zorder=3, label=label)
    ax.scatter([t_in - x0], [entry], s=120, marker="v" if d == -1 else "^", c=INK, zorder=4,
               label=f"your {'short' if d == -1 else 'long'} at {entry:.2f}")
    ax.scatter([t_out - x0], [exit_], s=90, marker="X", c=INK, zorder=4, label=f"exit {exit_:.2f} ({points:+.2f} pts)")

    ticks = np.arange(0, r1 - r0 + 1, 60)
    ax.set_xticks(ticks)
    ax.set_xticklabels([pd.Timestamp(x0 + k, unit="s", tz="UTC").tz_convert("America/New_York").strftime("%H:%M")
                        for k in ticks], color=MUTED)
    ax.tick_params(colors=MUTED, labelsize=9)
    for sp in ax.spines.values():
        sp.set_visible(False)
    ax.set_xlabel("time (ET)", color=MUTED)
    ax.set_ylabel("price", color=MUTED)
    ax.set_title(title, color=INK, loc="left", fontsize=12)
    cb = fig.colorbar(im, ax=ax, pad=0.01)
    cb.set_label("resting contracts at the price", color=MUTED)
    cb.ax.tick_params(colors=MUTED, labelsize=8)
    cb.outline.set_visible(False)
    ax.legend(loc="upper left", fontsize=8, frameon=True, facecolor=SURFACE, edgecolor=MUTED, labelcolor=INK)
    fig.tight_layout()
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)


def main() -> None:
    day, k = sys.argv[1], int(sys.argv[2])
    plan = pd.read_csv(OUT / "orderbook_price_plan.csv")
    r = plan[plan["day"] == day].iloc[0]
    t = pd.read_csv(OUT / "journal_trades.csv")
    for c in ("entry_time", "exit_time"):
        t[c] = pd.to_datetime(t[c], utc=True)
    t = t[t["closed"] & (t["entry_time"].dt.tz_convert("America/New_York").dt.date.astype(str) == day)]
    tr = t.sort_values("entry_time").iloc[k]
    start = int(pd.Timestamp(r["start"], tz="UTC").timestamp())
    end = int(pd.Timestamp(r["end"], tz="UTC").timestamp())
    book = build_book(load_mbo(MBO_DIR / f"{r['mes']}_{day}_mbo.dbn.zst"), start - LOOKBACK_S, end)
    CHARTS.mkdir(parents=True, exist_ok=True)
    when = tr["entry_time"].tz_convert("America/New_York")
    path = CHARTS / f"{day}_trade{k}.png"
    draw(book, tr["entry_time"].timestamp(), 1 if tr["side"] == "long" else -1, tr["entry"],
         tr["exit_time"].timestamp(), tr["exit"], tr["points"],
         f"{r['mes']} book rebuilt from Databento, {day}: your {tr['side']} at {when:%H:%M:%S} ET "
         f"({tr['market']} trade)", path)
    print("saved", path)


if __name__ == "__main__":
    main()
