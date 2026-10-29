"""Draw the bot's supply/demand zones on candles, for the owner to compare with their own marking.

Candles are built from the ES 1-minute bars (front contract, shown in that contract's own
prices). Zones are the bot's (research.supply_demand: chop of 3+ overlapping candles, then
a candle at least 2x the last chop candle's range that closes beyond the chop; zone = body
of the last chop candle; dies when a candle on its timeframe closes beyond the far side,
after 60 minutes of 1-minute closes inside it, or after about a month). Each zone is shaded
from when it was made until it died (or the chart ends). The recent uncovered POCs are drawn
as thin dashed lines.

Run from the repo root:   python -m research.zone_chart 2024-06-04 2024-06-06 [minutes]
"""

from __future__ import annotations

import sys

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

from research.journal_study import ES_BARS, ES_SYMBOLOGY, OUT  # noqa: E402
from research.level_study import prepare  # noqa: E402
from research.orderbook_chart import INK, MUTED, SURFACE  # noqa: E402
from research.recent_poc_study import recent_poc_labels  # noqa: E402
from research.settings import load_settings  # noqa: E402
from research.supply_demand import find_zones, make_candles, zone_lifetimes  # noqa: E402

DEMAND, SUPPLY = "#1baf7a", "#eb6834"


def main() -> None:
    first, last = sys.argv[1], sys.argv[2]
    minutes = int(sys.argv[3]) if len(sys.argv) > 3 else 15
    s = load_settings()
    bot = s["bot"]
    fb, sessions, _ = prepare(s, ES_BARS, ES_SYMBOLOGY, ("2024-01-16", "2025-03-21"), OUT.parent.parent.parent
                              / "data" / "raw" / "GLBX_condition_2024.json")
    candles = make_candles(fb, minutes)
    zones = find_zones(candles, bot["sd_base_min_candles"], bot["sd_departure_min_ratio"], bot["sd_zone_drawing"])
    zones = zone_lifetimes(zones, candles, fb, bot["sd_chop_invalid_minutes"],
                           bot["sd_lookback_months"] * s["level_study"]["sessions_per_month"], list(sessions.index))
    days = [d for d in sessions.index if pd.Timestamp(first) <= d <= pd.Timestamp(last)]
    off = int(sessions.loc[days[-1], "offset_ticks"])
    contract = sessions.loc[days[-1], "contract"]
    c = candles[candles["session"].isin(days)].reset_index(drop=True)
    t0, t1 = c["start"].min(), c["end"].max()
    tick = s["market"]["tick_size"]
    px = lambda v: (np.asarray(v) - off) * tick           # noqa: E731  adjusted ticks -> contract price
    live = zones[(zones["created"] < t1) & (zones["dies"] > t0)]

    fig, ax = plt.subplots(figsize=(16, 8), dpi=100, facecolor=SURFACE)
    ax.set_facecolor(SURFACE)
    x = np.arange(len(c))
    pos = {st: i for i, st in enumerate(c["start"])}
    def xi(ts):                                           # noqa: E306  candle index for a time
        return np.searchsorted(c["start"].to_numpy(), ts, "left")
    lo_view, hi_view = px(c["low"].min()) - 5, px(c["high"].max()) + 5
    for z in live.itertuples():
        a, b = xi(max(z.created, t0)), xi(min(z.dies, t1))
        colour = DEMAND if z.kind == "demand" else SUPPLY
        zl, zh = px(z.low), px(z.high)
        if zh < lo_view or zl > hi_view:
            continue
        ax.add_patch(Rectangle((a - 0.5, zl - 0.125), max(b - a, 1), zh - zl + 0.25, color=colour, alpha=0.28, lw=0, zorder=1))
        ax.text(a, zh + 0.3, f"{z.kind} {zl:.2f}-{zh:.2f}", fontsize=7, color=INK, zorder=4)
    for d in days:
        rec = recent_poc_labels(sessions, pd.Index([d]), bot["recent_poc_lookback_sessions"])
        sel = c.index[c["session"] == d]
        for p in rec.loc[~rec["covered"], "price"]:
            if lo_view <= px(p) <= hi_view:
                ax.hlines(px(p), sel.min() - 0.5, sel.max() + 0.5, colors=MUTED, linestyles="--", lw=0.8, zorder=2)
    up = c["close"] >= c["open"]
    for i, r in c.iterrows():
        ax.vlines(i, px(r.low), px(r.high), color=INK, lw=0.8, zorder=3)
        body_lo, body_hi = sorted([px(r.open), px(r.close)])
        ax.add_patch(Rectangle((i - 0.35, body_lo), 0.7, max(body_hi - body_lo, 0.05),
                               facecolor=SURFACE if up[i] else INK, edgecolor=INK, lw=0.7, zorder=3))
    starts = c.groupby("session").head(1)
    for i, r in starts.iterrows():
        ax.axvline(i - 0.5, color=MUTED, lw=0.8, ls=":", zorder=0)
        ax.text(i, hi_view - 1, pd.Timestamp(r.session).strftime("%a %d %b"), fontsize=9, color=INK)
    loc = pd.to_datetime(c["start"], unit="s", utc=True).dt.tz_convert("America/New_York")
    ticks = [i for i, t in enumerate(loc) if t.minute == 0 and t.hour % 3 == 0]
    ax.set_xticks(ticks)
    ax.set_xticklabels([loc[i].strftime("%H:%M") for i in ticks], fontsize=8, color=MUTED)
    ax.set_xlim(-1, len(c))
    ax.set_ylim(lo_view, hi_view)
    ax.tick_params(colors=MUTED, labelsize=8)
    for sp in ax.spines.values():
        sp.set_visible(False)
    ax.set_title(f"{contract} {minutes}-minute candles, full sessions (ET): the bot's supply (orange) and demand (green) "
                 f"zones; dashed = recent uncovered POCs", color=INK, loc="left", fontsize=11)
    path = OUT / "zone_charts" / f"bot_zones_{first}_{last}_{minutes}m.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(path, facecolor=SURFACE)
    print("saved", path, "| zones on the chart:", len(live))


if __name__ == "__main__":
    main()
