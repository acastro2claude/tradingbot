"""Exit styles on the owner's level tests (ES book, five 2024 weeks).

Takes every test that turned (research/results/journal/level_tests_es_weeks.csv,
from research.bot_week_study) and trades it with each exit style in
[[book_study.exits]] (settings.toml):
  - fixed stop and target from the fill (2/4, 3/7)
  - stop past the test's low or high, target a multiple of the risk
  - STRATEGY.md section 3 targets (first target = top of the last 1-minute chop
    before the move into the level, runner = low of that chop and breakout; 9 of
    10 contracts at the first target, runner stop locked 6 points in after it),
    with the stop past the test or 3 points past the level. Skipped if the first
    target is under 2x the risk (min_reward_to_risk).

Fills: market entry at the bid/ask + slippage; targets are limit orders filled
when price trades 1 tick through; stops and the close are market orders with
slippage (research.backtest.simulate_trade). Flat at 15:59 ET.

Reports, for each exit style and each read (bot rules A-E, plus line + stacking
+ delta), the per-test results, and the bot (one trade at a time, up to 3 a day)
against the owner week by week.

Needs data/derived/es_weeks_trades (written by research.bot_week_study).
Run from the repo root:   python -m research.exit_study
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd

from research.backtest import simulate_trade, targets
from research.bot_week_study import (CONTRACTS, ES_POINT, FLAT_ET, RULES, SLIP_TICKS, TICK, TRADES_DIR, bot_trades,
                                     et, matched_owner)
from research.journal_study import OUT
from research.level_test_study import owner_trades
from research.paths import trade_path
from research.settings import load_settings
from research.supply_demand import find_zones

EXTRA_READS = {
    "line before + stacking + delta": lambda e: (e["line_before"] >= 2) & (e["stacking"] > 0) & (e["delta_10s"] > 0),
    "line sat at one price 60 s+": lambda e: e["line_sat_s"] >= 60,
}


def minute_candles(tr: pd.DataFrame, day: str) -> pd.DataFrame:
    m = tr.assign(minute=(tr["t"] // 60) * 60).groupby("minute")["price"].agg(["first", "max", "min", "last"])
    c = pd.DataFrame(dict(open=m["first"], high=m["max"], low=m["min"], close=m["last"])).reset_index()
    c["start"] = c["minute"].astype(float)
    c["end"] = c["start"] + 60
    c["session"] = day
    return c.drop(columns="minute")


def day_context(es: str, day: str, s: dict) -> SimpleNamespace:
    tr = pd.read_parquet(TRADES_DIR / f"{es}_{day}.parquet").sort_values("t", kind="stable")
    p, t = trade_path(tr["price"].to_numpy(), tr["t"].to_numpy())
    c = minute_candles(tr, day)
    bot = s["bot"]
    chops = find_zones(c, bot["sd_base_min_candles"], bot["sd_departure_min_ratio"], "whole chop wicks")
    return SimpleNamespace(p=p, t=t, chops=chops.sort_values("created").reset_index(drop=True),
                           m_start=c["start"].to_numpy(), m_low=c["low"].to_numpy(), m_high=c["high"].to_numpy())


def one_exit(ctx, r, ex: dict, s: dict, flat: float) -> dict:
    """Trade one test with one exit style. Returns points per contract (after slippage), how, exit time."""
    d = int(r.direction)
    fill = int(r.entry_price) + d * SLIP_TICKS
    stop_pts = int(round(ex["stop_points"] / TICK))
    if ex["stop"] == "fixed":
        stop = fill - d * stop_pts
    elif ex["stop"] == "past test":
        stop = int(r.test_extreme) - d * stop_pts
    else:                                                    # past the level: below a long's level, above a short's
        stop = (int(r.lo) if d == 1 else int(r.hi)) - d * stop_pts
    risk = (fill - stop) * d
    if risk <= 0:
        return dict(taken=False, why="stop not behind the entry")
    first_contracts = CONTRACTS
    if ex["target"] == "fixed":
        ft = rt = fill + d * int(round(ex["target_points"] / TICK))
    elif ex["target"] == "risk multiple":
        ft = rt = fill + d * int(round(ex["target_multiple"] * risk))
    else:
        tg = targets(ctx, d, fill, r.entry_time)
        if tg is None:
            return dict(taken=False, why="no chop target")
        ft, rt = tg
        if (ft - fill) * d < s["bot"]["min_reward_to_risk"] * risk:
            return dict(taken=False, why="first target under 2x the risk")
        first_contracts = s["bot"]["first_target_contracts"]
    i0 = int(np.searchsorted(ctx.t, r.entry_time, "right"))
    if i0 >= len(ctx.p):
        return dict(taken=False, why="no data after entry")
    lock = int(round(s["bot"]["runner_stop_after_first_target_points"] / TICK))
    res = simulate_trade(ctx.p, ctx.t, i0, fill, d, stop, ft, rt, flat, SLIP_TICKS,
                         contracts=CONTRACTS, first_contracts=first_contracts, runner_lock=lock)
    return dict(taken=True, why=res.exit_reason, points=res.points / CONTRACTS,      # points per contract
                exit_time=res.exit_time, risk_points=risk * TICK)


def main() -> None:
    s = load_settings()
    exits = s["book_study"]["exits"]
    cost = CONTRACTS * s["backtest"]["commission_per_contract_round_trip"]
    tests = pd.read_csv(OUT / "level_tests_es_weeks.csv")
    plan = pd.read_csv(OUT / "es_week_price_plan.csv").set_index("day")
    e = tests[tests["turn"] == "turned"].copy()

    rows = []
    for day, g in e.groupby("day"):
        ctx = day_context(plan.loc[day, "es"], day, s)
        flat = et(day, FLAT_ET)
        for r in g.itertuples():
            for ex in exits:
                rows.append(dict(test=r.Index, exit=ex["name"], **one_exit(ctx, r, ex, s, flat)))
    res = pd.DataFrame(rows)
    res.to_csv(OUT / "exit_study_trades.csv", index=False)

    reads = dict(RULES)
    reads.update(EXTRA_READS)
    out = []
    for ex in exits:
        rx = res[res["exit"] == ex["name"]].set_index("test")
        for name, rule in reads.items():
            m = np.asarray(rule(e), bool)
            g = rx.loc[e.index[m]]
            g = g[g["taken"].astype(bool)]
            out.append(dict(exit=ex["name"], read=name, tests=int(m.sum()), traded=len(g),
                            win_rate=(g["points"] > 0).mean() if len(g) else np.nan,
                            avg_win=g.loc[g["points"] > 0, "points"].mean() if len(g) else np.nan,
                            avg_loss=g.loc[g["points"] <= 0, "points"].mean() if len(g) else np.nan,
                            usd_per_trade=g["points"].mean() * ES_POINT - cost if len(g) else np.nan))
    table = pd.DataFrame(out)
    table.to_csv(OUT / "exit_study_summary.csv", index=False)

    # bot vs owner per exit style (same 23 days), using each exit's own results and exit times
    owner = owner_trades()
    owner = owner[owner["day"].isin(e["day"].unique())].copy()
    owner["week"] = owner["day"].map(plan["week"])
    weeks = []
    for ex in exits:
        rx = res[res["exit"] == ex["name"]].set_index("test")
        te = tests.copy()
        te["points"] = rx["points"].reindex(te.index)
        te["exit_time"] = rx["exit_time"].reindex(te.index)
        te.loc[~rx["taken"].reindex(te.index, fill_value=False).astype(bool), "turn"] = "skipped by the exit rule"
        for name, rule in reads.items():
            if name not in RULES:
                continue
            b = bot_trades(te, rule, False)
            weeks.append(dict(exit=ex["name"], who=f"bot {name}", trades=len(b), win_rate=(b["points"] > 0).mean(),
                              net_usd=b["points"].sum() * ES_POINT - cost * len(b),
                              same_as_you=matched_owner(b, owner)))
    weeks.append(dict(exit="(your own exits)", who="you (journal)", trades=len(owner),
                      win_rate=(owner["points"] > 0).mean(), net_usd=owner["net_usd"].sum(), same_as_you=np.nan))
    bot = pd.DataFrame(weeks)
    bot.to_csv(OUT / "exit_study_bot_vs_owner.csv", index=False)

    pd.set_option("display.width", 250)
    print(table.round(2).to_string())
    print(bot.round(2).to_string())


if __name__ == "__main__":
    main()
