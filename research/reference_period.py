"""Re-run the main comparisons on the owner's disciplined period only (Apr 2024 - Mar 2025).

The owner says early 2024 lacked discipline, and the journal agrees (REPORT.md section 11).
This repeats, on [journal_rules] reference_start onward:
  1. bot vs owner on the ES weeks inside the period (Apr 8, Jun 3, Jul 29, Sep 23 2024), for
     each exit style in the exit study, and with the owner's two filters added to the bot
     (no entries inside the 09:30-09:45 range, none in the 10 minutes after a news spike)
  2. the order-book first look (MES, 20 busiest journal days), using only the days in the period
The day rules on the period come from: python -m research.journal_day_rules --from 2024-04-01
Writes research/results/journal/reference_bot_vs_owner.csv and reference_orderbook_summary.csv.

Run from the repo root:   python -m research.reference_period
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from research.bot_week_study import CONTRACTS, ES_POINT, RULES, bot_trades, matched_owner
from research.journal_day_rules import minutes, news_spikes
from research.journal_periods import opening_range
from research.journal_study import OUT
from research.level_test_study import owner_trades
from research.orderbook_study import matched_controls, summary_tables
from research.settings import load_settings

S = load_settings()
START = S["journal_rules"]["reference_start"]


def bot_filters(tests: pd.DataFrame, plan: pd.DataFrame) -> pd.Series:
    """True where a test passes the owner's filters: entry outside the 09:30-09:45 range, not just after news."""
    o = opening_range()
    loc = pd.to_datetime(tests["entry_time"], unit="s", utc=True).dt.tz_convert("America/New_York")
    m = loc.dt.hour * 60 + loc.dt.minute + loc.dt.second / 60
    key = pd.MultiIndex.from_arrays([tests["day"], tests["day"].map(plan["es"])])
    px = tests["entry_price"] * S["market"]["tick_size"]
    inside = (px <= o["orh"].reindex(key).to_numpy()) & (px >= o["orl"].reindex(key).to_numpy())
    sp = news_spikes()
    sp = sp[sp["spike"]]
    after = np.array([any(minutes(r.minute) <= mm < minutes(r.minute) + 10 for r in sp[sp["day"] == d].itertuples())
                      for d, mm in zip(tests["day"], m)])
    return pd.Series(~inside.to_numpy() & ~after, index=tests.index)


def main() -> None:
    cost = CONTRACTS * S["backtest"]["commission_per_contract_round_trip"]
    plan = pd.read_csv(OUT / "es_week_price_plan.csv").set_index("day")
    tests = pd.read_csv(OUT / "level_tests_es_weeks.csv")
    tests = tests[tests["day"] >= START].copy()
    res = pd.read_csv(OUT / "exit_study_trades.csv")
    owner = owner_trades()
    owner = owner[owner["day"].isin(tests["day"].unique())].copy()
    owner["week"] = owner["day"].map(plan["week"])
    passes = bot_filters(tests, plan)

    rows = [dict(exit="(your own exits)", who="you (journal)", trades=len(owner), win_rate=(owner["points"] > 0).mean(),
                 net_usd=owner["net_usd"].sum(), same_as_you=np.nan)]
    for exit_name in res["exit"].unique():
        rx = res[res["exit"] == exit_name].set_index("test")
        te = tests.copy()
        te["points"] = rx["points"].reindex(te.index)
        te["exit_time"] = rx["exit_time"].reindex(te.index)
        taken = rx["taken"].reindex(te.index, fill_value=False).astype(bool)
        te.loc[~taken, "turn"] = "skipped by the exit rule"
        for name, rule in RULES.items():
            for filt in (False, True):
                t2 = te.copy()
                if filt:
                    t2.loc[~passes, "turn"] = "filtered"
                b = bot_trades(t2, rule, False)
                rows.append(dict(exit=exit_name, who=f"bot {name}{' + your two filters' if filt else ''}",
                                 trades=len(b), win_rate=(b["points"] > 0).mean() if len(b) else np.nan,
                                 net_usd=b["points"].sum() * ES_POINT - cost * len(b),
                                 same_as_you=matched_owner(b, owner)))
    bot = pd.DataFrame(rows)
    bot.to_csv(OUT / "reference_bot_vs_owner.csv", index=False)

    # order book first look, days in the period only
    m = pd.read_csv(OUT / "orderbook_moments.csv")
    m = m[m["day"] >= START].reset_index(drop=True)
    rng = np.random.default_rng(S["level_study"]["random_seed"])
    share, _ = summary_tables(m, matched_controls(m), rng)
    share.to_csv(OUT / "reference_orderbook_summary.csv")

    pd.set_option("display.width", 250)
    print(f"ES weeks in the period: {sorted(tests['day'].unique())[0]} to {sorted(tests['day'].unique())[-1]}, "
          f"{tests['day'].nunique()} days")
    print(bot.sort_values("net_usd", ascending=False).head(12).round(2).to_string())
    print(bot[bot["who"].str.startswith("you") | bot["who"].isin(["bot A every level test", "bot A every level test + your two filters"])]
          .round(2).to_string())
    print(f"\norder book days in the period: {m['day'].nunique()}, your trades {int((m['kind'] == 'owner').sum())}")
    print((share.iloc[:-1] * 100).round(0).to_string())
    print(share.iloc[-1].to_string())


if __name__ == "__main__":
    main()
