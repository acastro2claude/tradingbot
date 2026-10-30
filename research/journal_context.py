"""What did the market look like at the owner's early vs later trades of the day?

For every journal trade, from the ES 1-minute bars of that contract that finished before
the entry minute (regular hours):
  - minutes since the open; the day's range so far (points)
  - how far the entry was from the day's high so far (for a short) or low (for a long)
  - the move into the entry over the last 15 minutes, and since the open (toward the entry
    = positive, i.e. price ran at the trade)
  - the average 1-minute bar range over the last 15 minutes (how fast the market was)
  - whether the entry was outside the 09:30-09:45 range
Compares the first 3 trades of a day with the 4th onward, and winners with losers, and
splits trades by where the entry was against the 09:30-09:45 range: inside it, fading a
breakout of it, or going with the breakout (with a 95% range from resampling days).
Writes research/results/journal/journal_context.csv and journal_opening_range.csv.

Run from the repo root:   python -m research.journal_context
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from research.journal_day_rules import load_journal
from research.journal_study import ES_BARS, ES_SYMBOLOGY, OUT
from research.market_data import load_bars
from research.settings import load_settings

S = load_settings()
TICK = S["market"]["tick_size"]
EARLY_TRADES = S["journal_rules"]["observe_first_trades"]
OPEN_RANGE_END = 9 * 60 + 45


def main() -> None:
    t = load_journal()
    t["n"] = t.groupby("day").cumcount() + 1
    t["late"] = t["n"] > EARLY_TRADES
    t["d"] = np.where(t["side"] == "long", 1, -1)
    t["es"] = "ES" + t["contract"].str.extract(r"([HMUZ])\d(\d)$").agg("".join, axis=1)
    b = load_bars(ES_BARS, ES_SYMBOLOGY)
    local = b["ts"].dt.tz_convert("America/New_York")
    b["day"] = local.dt.date.astype(str)
    b["m"] = local.dt.hour * 60 + local.dt.minute
    b = b[(b["m"] >= 570) & (b["m"] < 960)]
    rows = []
    for (day, es), g in t.groupby(["day", "es"]):
        bb = b[(b["day"] == day) & (b["contract"] == es)].sort_values("m")
        if bb.empty:
            continue
        m = bb["m"].to_numpy()
        hi, lo = bb["high"].to_numpy() * TICK, bb["low"].to_numpy() * TICK
        cl, op = bb["close"].to_numpy() * TICK, bb["open"].to_numpy() * TICK
        for r in g.itertuples():
            k = int(np.searchsorted(m, int(r.minute_of_day), "left"))   # bars finished before the entry minute
            if k < 5:
                continue
            first = m[:k] < OPEN_RANGE_END
            orh, orl = (hi[:k][first].max(), lo[:k][first].min()) if first.any() else (np.nan, np.nan)
            k15 = max(k - 15, 0)
            rows.append(dict(idx=r.Index, range_so_far=hi[:k].max() - lo[:k].min(),
                             to_day_extreme=(hi[:k].max() - r.entry) if r.d == -1 else (r.entry - lo[:k].min()),
                             move_in_15m=(cl[k - 1] - cl[k15]) * -r.d,
                             move_from_open=(cl[k - 1] - op[0]) * -r.d,
                             bar_range_15m=(hi[k15:k] - lo[k15:k]).mean(),
                             outside_open_range=bool(r.entry > orh or r.entry < orl) if not np.isnan(orh) else np.nan))
    x = t.join(pd.DataFrame(rows).set_index("idx"))
    x.to_csv(OUT / "journal_context.csv", index=False)
    cols = ["minute_of_day", "range_so_far", "to_day_extreme", "move_in_15m", "move_from_open", "bar_range_15m"]
    x["win"] = x["points"] > 0
    pd.set_option("display.width", 200)
    print("medians: first trades vs later trades")
    print(x.groupby("late")[cols].median().round(2).T.to_string())
    print("\nshares")
    print(x.groupby("late").agg(at_day_extreme=("to_day_extreme", lambda v: (v <= 2).mean()),
                                outside_open_range=("outside_open_range", "mean")).round(2).to_string())
    print("\nmedians: losers vs winners")
    print(x.groupby(["late", "win"])[cols].median().round(2).T.to_string())

    # where the entry was against the full 09:30-09:45 range
    orr = (b[(b["m"] >= 570) & (b["m"] < OPEN_RANGE_END)].groupby(["day", "contract"])
           .agg(orh=("high", "max"), orl=("low", "min")) * TICK)
    x = x.join(orr, on=["day", "es"])
    above, below = x["entry"] > x["orh"], x["entry"] < x["orl"]
    short = x["side"] == "short"
    x["opening_range"] = np.select([(above & short) | (below & ~short), above | below],
                                   ["fade the breakout", "with the breakout"], "inside the 09:30-09:45 range")
    x["half"] = np.where(x["day"] < S["journal_rules"]["split_date"], "first half", "second half")
    summary = x.groupby(["late", "opening_range"]).agg(trades=("points", "size"), win=("win", "mean"),
                                                       net_usd=("net_usd", "sum"), pts=("points", "mean"))
    by_half = x.groupby(["opening_range", "half"])["net_usd"].sum().unstack()
    inside = x["opening_range"] == "inside the 09:30-09:45 range"
    rng = np.random.default_rng(S["level_study"]["random_seed"])
    days, xs = x["day"].unique(), x.set_index("day")
    diffs = []
    for _ in range(S["level_study"]["bootstrap_samples"]):
        smp = xs.loc[rng.choice(days, len(days))]
        ins = smp["opening_range"] == "inside the 09:30-09:45 range"
        diffs.append(smp.loc[~ins, "points"].mean() - smp.loc[ins, "points"].mean())
    lo, hi = np.percentile(diffs, [2.5, 97.5])
    keep = x[~inside]
    summary.to_csv(OUT / "journal_opening_range.csv")
    print("\nby the 09:30-09:45 range (first 3 trades = False, later = True)")
    print(summary.round(2).to_string())
    print(by_half.round(0).to_string())
    print(f"outside minus inside, points per contract: 95% range {lo:+.2f} to {hi:+.2f}")
    pf = keep["net_usd"][keep["net_usd"] > 0].sum() / -keep["net_usd"][keep["net_usd"] < 0].sum()
    print(f"skip entries inside the range: {len(keep)} trades, net ${keep['net_usd'].sum():,.0f} "
          f"(as traded ${x['net_usd'].sum():,.0f}), profit factor {pf:.2f}")


if __name__ == "__main__":
    main()
