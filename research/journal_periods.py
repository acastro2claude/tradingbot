"""The owner's journal over time: did the later period trade with more discipline? (Oct 2026)

Owner: "earlier in 2024 I did not have as much discipline; around 2025 it is more the case."
Splits the journal (research/results/journal/journal_context.csv, from research.journal_context)
by quarter and reports, per quarter: days traded, trades per day, days with 4+ trades, win
rate, average win and loss, net $, $ per trade, profit factor, the share of entries inside
the 09:30-09:45 range and what those made, and the first-3 vs later trades.
Writes research/results/journal/journal_by_quarter.csv.

Run from the repo root:   python -m research.journal_periods
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from research.journal_study import ES_BARS, ES_SYMBOLOGY, OUT
from research.market_data import load_bars
from research.settings import load_settings

S = load_settings()
TICK = S["market"]["tick_size"]


def opening_range() -> pd.DataFrame:
    b = load_bars(ES_BARS, ES_SYMBOLOGY)
    loc = b["ts"].dt.tz_convert("America/New_York")
    b = b.assign(day=loc.dt.date.astype(str), m=loc.dt.hour * 60 + loc.dt.minute)
    o = b[(b["m"] >= 570) & (b["m"] < 585)].groupby(["day", "contract"]).agg(orh=("high", "max"), orl=("low", "min"))
    return o * TICK


def pf(v: pd.Series) -> float:
    loss = -v[v < 0].sum()
    return v[v > 0].sum() / loss if loss else np.nan


def main() -> None:
    x = pd.read_csv(OUT / "journal_context.csv")
    x = x.join(opening_range(), on=["day", "es"])
    x["inside_or"] = (x["entry"] <= x["orh"]) & (x["entry"] >= x["orl"])
    x["quarter"] = pd.PeriodIndex(pd.to_datetime(x["day"]), freq="Q").astype(str)
    x["day_trades"] = x.groupby("day")["net_usd"].transform("size")
    rows = []
    for q, g in x.groupby("quarter"):
        daily = g.groupby("day")["net_usd"].sum()
        w, lo = g[g["points"] > 0], g[g["points"] < 0]
        ins = g[g["inside_or"]]
        early = g[~g["late"]]
        rows.append(dict(
            quarter=q, days=g["day"].nunique(), trades=len(g),
            trades_per_day=len(g) / g["day"].nunique(),
            days_with_4_plus=(g.groupby("day").size() >= 4).mean(),
            win_rate=(g["points"] > 0).mean(), avg_win_pts=w["points"].mean(), avg_loss_pts=lo["points"].mean(),
            net_usd=g["net_usd"].sum(), usd_per_trade=g["net_usd"].mean(), profit_factor=pf(g["net_usd"]),
            winning_days=(daily > 0).mean(), worst_day=daily.min(),
            share_inside_or=g["inside_or"].mean(), net_inside_or=ins["net_usd"].sum(),
            net_outside_or=g.loc[~g["inside_or"], "net_usd"].sum(),
            net_first_3=early["net_usd"].sum(), net_first_3_inside_or=early.loc[early["inside_or"], "net_usd"].sum(),
            net_later=g.loc[g["late"], "net_usd"].sum()))
    out = pd.DataFrame(rows)
    out.to_csv(OUT / "journal_by_quarter.csv", index=False)
    pd.set_option("display.width", 250)
    print(out.round(2).T.to_string())


if __name__ == "__main__":
    main()
