"""Draw the bot's course-style supply/demand zones (research.zones) on candles, for the owner to check.

Each zone is shaded lightly from its base until the bot knew it (price had moved far enough
away), then fully until it stopped counting (broken, chopped in, expired). Every test is
circled and numbered; from the 4th test on (weak_from_test) the circle is dashed: the zone
is on its way out. Zones a higher-timeframe zone later formed over are left off: the chart
shows the zones as marked in hindsight. Prices are in the contract of the chart's last day;
times are Eastern.

Overlapping zones are already resolved (research.zones.resolve_overlaps): the higher
timeframe wins, and a lower-timeframe zone that a higher one formed over just stops.
Darker shading = higher timeframe.

Run from the repo root:
    python -m research.zone_course_chart 2024-09-23 2024-09-27 30 [zone timeframes, default all]
"""

from __future__ import annotations

import sys

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch, Rectangle  # noqa: E402

from research.journal_study import ES_BARS, ES_SYMBOLOGY, OUT, REPO  # noqa: E402
from research.level_study import prepare  # noqa: E402
from research.orderbook_chart import INK, MUTED, SURFACE  # noqa: E402
from research.settings import load_settings  # noqa: E402
from research.zones import all_zones  # noqa: E402

DEMAND, SUPPLY = "#1baf7a", "#eb6834"
NAMES = {15: "15m", 30: "30m", 60: "1h", 120: "2h", 240: "4h"}


def es_zones(s: dict):
    """ES 2024-25 front-contract bars, sessions and the course-style zones on them."""
    fb, sessions, _ = prepare(s, ES_BARS, ES_SYMBOLOGY, ("2024-01-16", "2025-03-21"),
                              REPO / "data" / "raw" / "GLBX_condition_2024.json")
    z, candles = all_zones(fb, s)
    return fb, sessions, z, candles


def draw(first: str, last: str, tf: int, zone_tfs: list[int], s: dict, sessions, z, candles,
         highlight: tuple[float, float] | None = None, path=None) -> str:
    days = [d for d in sessions.index if pd.Timestamp(first) <= d <= pd.Timestamp(last)]
    off = int(sessions.loc[days[-1], "offset_ticks"])
    contract = sessions.loc[days[-1], "contract"]
    tick = s["market"]["tick_size"]
    c = candles[tf]
    c = c[c["session"].isin(days)].reset_index(drop=True)
    t0, t1 = c["start"].min(), c["end"].max()
    px = lambda v: (np.asarray(v) - off) * tick           # noqa: E731  adjusted ticks -> contract price
    starts = c["start"].to_numpy()

    def xi(t):                                            # x position (candle index, fractional) of a time
        i = int(np.clip(np.searchsorted(starts, t, "right") - 1, 0, len(c) - 1))
        return i - 0.5 + min(max((t - starts[i]) / (tf * 60), 0), 1)

    lo_view, hi_view = px(c["low"].min()) - 10, px(c["high"].max()) + 10
    live = z[z["timeframe"].isin(zone_tfs) & (z["created"] < t1) & (z["dies"] > t0)
             & (z["why"] != "replaced by a higher timeframe")]
    weak = s["zones"]["weak_from_test"]
    live = live[(px(live["high"]) >= lo_view) & (px(live["low"]) <= hi_view)]

    fig, ax = plt.subplots(figsize=(17, 8.5), dpi=100, facecolor=SURFACE)
    ax.set_facecolor(SURFACE)
    for r in live.sort_values("created").itertuples():
        colour = DEMAND if r.kind == "demand" else SUPPLY
        zl, zh = px(r.low), px(r.high)
        a = xi(max(r.base_start, t0))
        b = xi(min(r.dies, t1))
        rank = sorted(zone_tfs).index(r.timeframe) / max(len(zone_tfs) - 1, 1)   # 0 = lowest timeframe
        strong = 0.12 + 0.22 * rank
        m = xi(min(max(r.created, t0), t1))                                   # the move away finished here
        ax.add_patch(Rectangle((a, zl), m - a, zh - zl, color=colour, alpha=strong * 0.35, lw=0, zorder=1))
        ax.add_patch(Rectangle((m, zl), b - m, zh - zl, color=colour, alpha=strong, lw=0, zorder=1))
        for n, tt in enumerate(r.tests, 1):
            if t0 <= tt < t1:
                ax.plot(xi(tt), px(r.near), "o", ms=12, mfc=SURFACE, mec=INK, mew=1.2, zorder=5,
                        ls="none", alpha=1 if n < weak else 0.55)
                ax.text(xi(tt), px(r.near), str(n), fontsize=6.5, ha="center", va="center", color=INK, zorder=6)
        mine = highlight is not None and abs(zl - highlight[0]) < 1 and abs(zh - highlight[1]) < 1
        label = f"{NAMES.get(r.timeframe, r.timeframe)} {r.pattern} {zl:.2f}-{zh:.2f}" + ("  <- your example" if mine else "")
        y = min(zh + 0.3, hi_view - 2) if r.kind == "supply" else max(zl - 0.3, lo_view + 2)
        ax.text(a + 0.2, y, label, fontsize=6.5 if len(zone_tfs) > 2 else 7.5,
                va="bottom" if r.kind == "supply" else "top", color=INK, zorder=6,
                fontweight="bold" if mine else "normal")

    up = (c["close"] >= c["open"]).to_numpy()
    for i, r in c.iterrows():
        ax.vlines(i, px(r.low), px(r.high), color=INK, lw=0.8, zorder=3)
        body_lo, body_hi = sorted([px(r.open), px(r.close)])
        ax.add_patch(Rectangle((i - 0.33, body_lo), 0.66, max(body_hi - body_lo, 0.1),
                               facecolor=SURFACE if up[i] else INK, edgecolor=INK, lw=0.7, zorder=3))
    first_of_day = c.groupby("session").head(1)
    for i in first_of_day.index:
        ax.axvline(i - 0.5, color=MUTED, lw=0.5, ls=":", alpha=0.6, zorder=0)
    weekly = len(days) > 10                               # long charts: label Mondays only
    ticks = [(i, pd.Timestamp(r.session)) for i, r in first_of_day.iterrows()
             if not weekly or pd.Timestamp(r.session).dayofweek == 0 or i == 0]
    ax.set_xticks([i - 0.5 for i, _ in ticks])
    ax.set_xticklabels([d.strftime("%a %d %b") for _, d in ticks], fontsize=8, color=MUTED, ha="left")
    ax.set_xlim(-1, len(c))
    ax.set_ylim(lo_view, hi_view)
    ax.tick_params(colors=MUTED, labelsize=8)
    ax.yaxis.tick_right()
    for sp in ax.spines.values():
        sp.set_visible(False)
    handles = [Patch(color=DEMAND, alpha=0.3, label="demand zone"),
               Patch(color=SUPPLY, alpha=0.3, label="supply zone"),
               Line2D([], [], marker="o", ls="none", mfc="none", mec=INK, ms=10, label="test number (faint from the 4th)"),
               Patch(color=MUTED, alpha=0.3, label="darker = higher timeframe"),
               Patch(color=MUTED, alpha=0.1, label="light = still forming (move away not finished)")]
    ax.legend(handles=handles[:3] + (handles[3:] if len(zone_tfs) > 1 else []), loc="upper center",
              bbox_to_anchor=(0.5, -0.04), fontsize=8.5, frameon=False, labelcolor=INK, ncol=5)
    tfs = " + ".join(NAMES.get(m, str(m)) for m in zone_tfs)
    ax.set_title(f"ES {contract}, {NAMES.get(tf, tf)} candles ({first} to {last}, Chicago-midnight candles like your "
                 f"thinkorswim). The bot's {tfs} zones, course rules.", color=INK, loc="left", fontsize=11)
    path = path or OUT / "zone_charts" / f"course_zones_{first}_{last}_{NAMES.get(tf, tf)}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)
    print("saved", path, "| zones drawn:", len(live))
    return str(path)


def main() -> None:
    first, last = sys.argv[1], sys.argv[2]
    tf = int(sys.argv[3]) if len(sys.argv) > 3 else 240
    s = load_settings()
    zone_tfs = [int(v) for v in sys.argv[4].split(",")] if len(sys.argv) > 4 else s["zones"]["timeframes_minutes"]
    _, sessions, z, candles = es_zones(s)
    draw(first, last, tf, zone_tfs, s, sessions, z, candles)


if __name__ == "__main__":
    main()
