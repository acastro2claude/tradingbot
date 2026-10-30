"""The owner's real journal trades re-run with fixed brackets: 4-point vs 5-point stop, 10-point target,
with and without 2 trades a day. Settings: [replay_stops].

The replay page gave only 59 trades, and its clips of one day were seen out of order, so a daily
cap can't be judged there. The journal (Jan 2024 - Mar 2025) has the owner's real entries in real
order: each trade's entry time, price and side are kept, and the exit is replaced by a fixed
bracket, run on ES 1-minute bars of the same contract month (MES trades use the ES bars; the
prices match):
  - the bracket starts on the bar after the entry minute; a bar that reaches both the stop and the
    target counts as a loss; stops and the 15:59 close pay 1 tick; the target counts when touched
  - 2 a day: per day in entry order, one trade at a time, at most 2 (owner: win or lose). Also shown
    for entries from 10:00 only (the proposed "no entries before 10:00" rule)
  - money: 10 MES ($50 a point), $12.40 costs per trade, also for the owner's own exits
  - trades in another contract month than the bars (around rolls) or after 15:59 are left out
The owner's method is judged from Q2 2024 on (REPORT.md section 11).

Writes research/results/journal/journal_stops.csv and journal_stops_summary.csv.
Run from the repo root:   python -m research.journal_stops
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from research.bot_week_study import CONTRACTS, ES_POINT, FLAT_ET, SLIP_TICKS, et
from research.journal_day_rules import load_journal
from research.journal_study import OUT
from research.market_data import epoch_seconds
from research.replay_stops import cap_per_day
from research.settings import load_settings
from research.zone_all_days import run_exit
from research.zone_course_chart import es_zones

TICK = 0.25


def es_month(contract: str) -> str:
    """ESH24 / MESH24 -> ESH4 (the bars' symbol)."""
    c = contract[1:] if contract.startswith("MES") else contract
    return c[:3] + c[-1]


def main() -> None:
    s = load_settings()
    R = s["replay_stops"]
    cost = CONTRACTS * s["backtest"]["commission_per_contract_round_trip"]
    stops = sorted(set(R["stop_curve"]) | {e[0] for e in R["exits"]})
    target = R["exits"][0][1]
    fb, _, _, _ = es_zones(s)
    t = epoch_seconds(fb["ts"])
    lo, hi, cl = (fb[k].to_numpy() for k in ("low", "high", "close"))
    con, ses = fb["contract"].to_numpy(), fb["session"].to_numpy()

    j = load_journal()
    rows, dropped = [], {}
    for r in j.itertuples():
        te = r.entry_time.timestamp()
        b = int(np.searchsorted(t, te, "right")) - 1
        if b < 0 or con[b] != es_month(r.contract):
            dropped["other contract month or no bars"] = dropped.get("other contract month or no bars", 0) + 1
            continue
        flat = et(pd.Timestamp(ses[b]).date().isoformat(), FLAT_ET)
        end = int(np.searchsorted(t, flat, "right")) - 1
        if te >= flat or end <= b:
            dropped["after 15:59"] = dropped.get("after 15:59", 0) + 1
            continue
        d = 1 if r.side == "long" else -1
        fill = round(r.entry / TICK) + d * SLIP_TICKS
        base = dict(trade_id=r.trade_id, day=r.day, entry_time=te, quarter=pd.Timestamp(r.day).to_period("Q").strftime("%Y Q%q"))
        rows.append(dict(base, exit="your exits", points=r.points, exit_time=r.exit_time.timestamp()
                         if hasattr(r.exit_time, "timestamp") else pd.Timestamp(r.exit_time).timestamp()))
        for st in stops:
            ticks, k = run_exit(lo, hi, cl, b, end, fill, d, round(st / TICK), round(target / TICK), SLIP_TICKS)
            rows.append(dict(base, exit=f"{st:g}/{target:g}", points=ticks * TICK, exit_time=float(t[k]) + 60))
    x = pd.DataFrame(rows)
    x["usd"] = x["points"] * ES_POINT - cost
    x["from_q2_2024"] = x["day"] >= "2024-04-01"
    x["from_10am"] = pd.to_datetime(x["entry_time"], unit="s", utc=True).dt.tz_convert("America/New_York").dt.hour >= 10
    x.to_csv(OUT / "journal_stops.csv", index=False)

    out = []
    periods = {"Jan 2024 - Mar 2025": x["day"].notna(), "from Q2 2024": x["from_q2_2024"],
               "from Q2 2024, entries from 10:00": x["from_q2_2024"] & x["from_10am"]}
    for period, pm in periods.items():
        for ex, g in x[pm].groupby("exit", sort=False):
            for cap in ("none", f"{R['max_trades_per_day']} a day"):
                v = g if cap == "none" else g[cap_per_day(g, R["max_trades_per_day"])]
                q = v.groupby("quarter")["usd"].sum()
                out.append(dict(period=period, exit=ex, cap=cap, trades=len(v), win_rate=(v["points"] > 0).mean(),
                                usd_per_trade=v["usd"].mean(), net_usd=v["usd"].sum(),
                                quarters_up=f"{int((q > 0).sum())} of {len(q)}"))
    table = pd.DataFrame(out)
    table.to_csv(OUT / "journal_stops_summary.csv", index=False)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_rows", 100)
    print("left out:", dropped, "| kept", x["trade_id"].nunique(), "of", len(j))
    print(table.round(2).to_string())
    y = x[x["from_q2_2024"]].pivot_table(index="trade_id", columns="exit", values="points")
    print("from Q2 2024: stopped at 4 but +10 at 5:", int(((y["4/10"] < 0) & (y["5/10"] >= 10)).sum()),
          "| won at 4, stopped at 5:", int(((y["4/10"] >= 10) & (y["5/10"] < 0)).sum()), "| trades", len(y))
    print(x[x["from_q2_2024"] & x["exit"].isin(["4/10", "5/10"])].pivot_table(index="quarter", columns="exit", values="usd",
                                                                               aggfunc="sum").round(0).to_string())


if __name__ == "__main__":
    main()
