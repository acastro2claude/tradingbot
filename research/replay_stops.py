"""A wider stop on the owner's replay trades: 4-point vs 5-point stop, 10-point target, 2 trades a day.

Owner (Oct 2026): "Some of the trades I took I noticed would hit a close stop loss, but then would
continue in my direction... a 5 point stop to a 10 point reward? Two trade a day max regardless of
win or lose." Settings: [replay_stops].

Trades: the owner's long/short decisions on the zone replay page (research.replay_score: entry at
the price when they pressed + 1 tick; a second look replaces the first). Same day = same calendar
day across ES and MES. Per day, in entry order: one trade at a time, at most 2 trades. The clips of
one day were seen out of order and often overlap, so the cap is a rough check here; the journal
(research.journal_stops) is the better test of it. Also the
bot's own entry (first 1-point turn) on every clip decided, with the same exits and cap.
"Quick" = the 45 clips of the second sitting decided about one a minute without notes; the rest
is "careful".

Also: for each owner trade that reached +10 before the close, how far it went against the entry
first (the stop it needed).

Writes research/results/journal/replay_stops.csv (one row per trade and exit) and
replay_stops_summary.csv. Run from the repo root:   python -m research.replay_stops
"""

from __future__ import annotations

import glob
import json

import numpy as np
import pandas as pd

from research.bot_week_study import CONTRACTS, ES_POINT, FLAT_ET, SLIP_TICKS, et
from research.journal_study import OUT
from research.level_test_book import TestReader
from research.replay_score import DECISIONS, TICK, fixed_exit_t, load_day, sample
from research.settings import load_settings


def heat_before_target(dt, t_in: float, fill: int, d: int, flat: float, tgt_pts: float) -> float:
    """Points against the entry before the target is reached; NaN if it never is."""
    i, j = int(np.searchsorted(dt.t, t_in, "right")), int(np.searchsorted(dt.t, flat, "right"))
    px = dt.px[i:j]
    k = np.flatnonzero((px - (fill + d * round(tgt_pts / TICK))) * d >= 1)
    if not len(k):
        return np.nan
    return float(max(0, ((fill - px[:k[0] + 1]) * d).max())) * TICK if k[0] > 0 else 0.0


def cap_per_day(x: pd.DataFrame, n_max: int) -> pd.Series:
    """True for the trades kept: per day in entry order, one at a time, at most n_max."""
    keep = pd.Series(False, index=x.index)
    for _, g in x.sort_values("entry_time").groupby("day"):
        busy, n = -np.inf, 0
        for i, r in g.iterrows():
            if n < n_max and r.entry_time >= busy:
                keep[i], busy, n = True, r.exit_time, n + 1
    return keep


def main() -> None:
    s = load_settings()
    R = s["replay_stops"]
    cost = CONTRACTS * s["backtest"]["commission_per_contract_round_trip"]
    exits = sorted({tuple(e) for e in R["exits"]} | {(st, R["exits"][0][1]) for st in R["stop_curve"]})
    dec = pd.DataFrame([json.load(open(f)) for f in sorted(glob.glob(str(DECISIONS / "*.json")))])
    d = dec.merge(pd.read_csv(OUT / "replay_clip_ids.csv"), left_on="clip_id", right_on="public_id")
    d = d.merge(pd.read_csv(OUT / "replay_tests.csv"), on="id")
    d = d[d["decision"].isin(["long", "short", "skip", "unsure"])]
    d = d.sort_values("round").drop_duplicates("id", keep="last")
    rows, heat = [], []
    for (market, contract, day), g in d.groupby(["market", "contract", "day"]):
        dt, flat = load_day(market, contract, day), et(day, FLAT_ET)
        for r in g.itertuples():
            smp = sample(r.saved_at, r.round)
            quick = smp == "new clips, second sitting" and not str(r.note or "").strip()
            base = dict(clip=r.clip_id, day=day, sample=smp, careful=not quick, decision=r.decision)
            entries = []
            if r.decision in ("long", "short"):
                od = 1 if r.decision == "long" else -1
                entries.append(("you", r.entry_time, round(r.entry_price / TICK) + od * SLIP_TICKS, od))
            k, _ = TestReader.turn_trade(dt, r.time, int(r.direction), int(r.edge))
            if k is not None:
                zd = int(r.direction)
                entries.append(("bot", float(dt.t[k]), int(dt.px[k]) + zd * SLIP_TICKS, zd))
            for who, t_in, fill, dd in entries:
                for st, tg in exits:
                    pts, t_out = fixed_exit_t(dt, t_in, fill, dd, flat, st, tg)
                    rows.append(dict(base, who=who, exit=f"{st:g}/{tg:g}", stop=st, entry_time=t_in, exit_time=t_out,
                                     points=pts * TICK))
                if who == "you":
                    heat.append(dict(base, heat_before_10=heat_before_target(dt, t_in, fill, dd, flat, 10.0)))
    x = pd.DataFrame(rows)
    x["usd"] = x["points"] * ES_POINT - cost
    x.to_csv(OUT / "replay_stops.csv", index=False)

    out = []
    groups = {"all": x["who"].notna(), "careful": x["careful"], "quick (last 45 clips)": ~x["careful"]}
    for gname, gm in groups.items():
        for (who, ex), g in x[gm].groupby(["who", "exit"], sort=False):
            for capped in (True, False):
                v = g[cap_per_day(g, R["max_trades_per_day"])] if capped else g          # the cap within this sample
                out.append(dict(sample=gname, who=who, exit=ex, cap=f"{R['max_trades_per_day']} a day" if capped else "none",
                                trades=len(v), win_rate=(v["points"] > 0).mean(), usd_per_trade=v["usd"].mean(),
                                net_usd=v["usd"].sum()))
    table = pd.DataFrame(out)
    table.to_csv(OUT / "replay_stops_summary.csv", index=False)

    pd.set_option("display.width", 250)
    pd.set_option("display.max_rows", 200)
    show = table[table["exit"].isin([f"{a:g}/{b:g}" for a, b in R["exits"]])]
    print(show.round(2).to_string())
    print(table[(table["who"] == "you") & (table["cap"] != "none") & (table["sample"] != "quick (last 45 clips)")].round(2).to_string())
    y = x[(x["who"] == "you")].pivot_table(index="clip", columns="exit", values="points")
    print("your trades stopped at 4 that reached +10 with a 5-point stop:", int(((y["4/10"] < 0) & (y["5/10"] > 0)).sum()),
          "; won at 4 but lost at 5:", int(((y["4/10"] > 0) & (y["5/10"] < 0)).sum()), "; of", len(y))
    h = pd.DataFrame(heat)
    h.to_csv(OUT / "replay_stops_heat.csv", index=False)
    r10 = h["heat_before_10"].dropna()
    print(f"your trades that reached +10 before the close: {len(r10)} of {len(h)}")
    print(pd.cut(r10, [-0.01, 2, 3, 4, 5, 6, 8, 100]).value_counts().sort_index().to_string())


if __name__ == "__main__":
    main()
