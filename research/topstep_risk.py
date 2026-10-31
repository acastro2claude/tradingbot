"""Which daily rule keeps the owner off Topstep's loss limit, and what it costs.

Owner (Oct 2026): Topstep closes the account at its loss limit, and "the 2 cap a day is to prevent
that from happening". This replays the owner's real journal trades (research.journal_stops: real
entries; the owner's own exits or a fixed 4/10 or 5/10 bracket; 10 or 5 MES, $1.24 costs per
contract) through a Topstep account. Settings: [topstep_risk].

Accounts ([topstep_risk] accounts): 100K (limit $3,000, target $6,000, daily limit $2,000; the owner's
last Combine) and 50K ($2,000, $3,000, $1,000).
Per day, in entry order, one trade at a time; the day ends at the first of:
  - the day rule: none / 2 trades taken / 2 losing trades / -$500 or -$1,000 on the day
  - Topstep's daily loss limit (counted on closed trades)
The account: Maximum Loss Limit = best end-of-day balance minus the limit. The owner: it always
trails by the same amount ([topstep_risk] mll_stops_at_start_balance = false); some Topstep pages
say it stops rising at the starting balance. A closed trade at or below it loses the account; it is counted, and a new account starts
the next day. Open-trade drawdown is not counted, so real losses come a little sooner.

Combine odds: one 11-month history gives only a handful of lost accounts, so the trading days of
each rule are also drawn at random (with replacement, whole days kept intact) to play 10,000
Combines: pass = the balance closes a day at the profit target ($3,000 on 50K, $6,000 on 100K)
before it touches the Maximum Loss Limit; still open after 500 trading days counts as neither. Drawing days at random ignores streaks of bad days that
cluster in real markets, so real odds are somewhat worse. Topstep's consistency target (best day at
most half the profit) is not modelled.

Writes research/results/journal/topstep_risk.csv. Run:   python -m research.topstep_risk
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from research.journal_study import OUT
from research.settings import load_settings


def day_filter(g: pd.DataFrame, rule: str, dll: float) -> list[bool]:
    """Per day: "none" (only the account's daily loss limit), "2 trades", "2 losses" or "-X on the day"."""
    cut = float(rule.split()[0]) if rule.endswith("on the day") else None
    keep, busy, n, losses, pnl = [], -np.inf, 0, 0, 0.0
    for r in g.itertuples():
        stop = (rule == "2 trades" and n >= 2) or (rule == "2 losses" and losses >= 2) \
            or (cut is not None and pnl <= cut) or pnl <= -dll
        if stop or r.entry_time < busy:
            keep.append(False)
            continue
        keep.append(True)
        busy, n, pnl, losses = r.exit_time, n + 1, pnl + r.usd, losses + (r.usd < 0)
    return keep


def floor_of(peak: float, start: float, mll: float, lock: bool) -> float:
    return min(peak - mll, start) if lock else peak - mll


def account(trades: pd.DataFrame, start: float, mll: float, lock: bool) -> tuple[int, float]:
    """Accounts lost and the largest drop from a best end-of-day balance."""
    bal, peak, lost, worst = start, start, 0, 0.0
    for _, g in trades.groupby("day", sort=True):
        floor = floor_of(peak, start, mll, lock)
        blown = False
        for u in g["usd"]:
            bal += u
            worst = max(worst, peak - bal)
            if bal <= floor:
                blown = True
                break
        if blown:
            lost, bal, peak = lost + 1, start, start
        else:
            peak = max(peak, bal)
    return lost, worst


def combine_odds(k: pd.DataFrame, start: float, mll: float, target: float, n: int, max_days: int,
                 rng, lock: bool) -> tuple[float, float, float]:
    """Share of simulated Combines passed and failed, and the median days to pass."""
    days = [g["usd"].to_numpy() for _, g in k.groupby("day", sort=True)]
    if not days:
        return np.nan, np.nan, np.nan
    passed, failed, took = 0, 0, []
    for _ in range(n):
        bal, peak = start, start
        for day_no, i in enumerate(rng.integers(0, len(days), max_days), 1):
            floor = floor_of(peak, start, mll, lock)
            if (bal + np.cumsum(days[i]) <= floor).any():
                failed += 1
                break
            bal += days[i].sum()
            peak = max(peak, bal)
            if bal >= start + target:
                passed += 1
                took.append(day_no)
                break
    return passed / n, failed / n, float(np.median(took)) if took else np.nan


def main() -> None:
    s = load_settings()
    T = s["topstep_risk"]
    seed = s["level_study"]["random_seed"]
    lock = T["mll_stops_at_start_balance"]
    x = pd.read_csv(OUT / "journal_stops.csv")
    x = x[x["from_q2_2024"] & x["exit"].isin(T["exits"])].sort_values("entry_time")
    rows = []
    for acc in T["accounts"]:
        for size in T["sizes_mes"]:
            for entries, em in {"entries from 10:00": x["from_10am"], "all entries": x["day"].notna()}.items():
                y = x[em].copy()
                y["usd"] = (y["points"] * s["market"]["point_value_usd"] * size
                            - s["backtest"]["commission_per_contract_round_trip"] * size)
                for ex, g in y.groupby("exit", sort=False):
                    for rule in T["day_rules"]:
                        keep = []
                        for _, d in g.groupby("day", sort=True):
                            keep += [i for i, ok in zip(d.index, day_filter(d, rule, acc["daily_loss"])) if ok]
                        k = g.loc[keep]
                        days = k.groupby("day")["usd"].sum()
                        lost, worst = account(k, acc["start"], acc["max_loss"], lock)
                        p_pass, p_fail, med = combine_odds(k, acc["start"], acc["max_loss"], acc["target"],
                                                           T["combine_sims"], T["combine_max_days"],
                                                           np.random.default_rng(seed), lock)
                        rows.append(dict(account=acc["name"], mes=size, entries=entries, exit=ex, day_rule=rule,
                                         trades=len(k), net_usd=k["usd"].sum(), usd_per_day=days.mean(),
                                         worst_day=days.min(), accounts_lost_real_order=lost,
                                         worst_drop_from_high=worst, combine_pass=p_pass, combine_fail=p_fail,
                                         still_open=1 - p_pass - p_fail, median_days_to_pass=med))
    t = pd.DataFrame(rows)
    t.to_csv(OUT / "topstep_risk.csv", index=False)
    pd.set_option("display.width", 300)
    pd.set_option("display.max_rows", 200)
    print(t.round(2).to_string())


if __name__ == "__main__":
    main()
