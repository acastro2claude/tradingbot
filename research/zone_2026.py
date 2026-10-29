"""Out-of-sample check on 2026: the bot at the owner-style zones, MES trades Apr-Oct 2026.

On the 23 ES days of 2024 (research.zone_book_study) one version made money: every zone
test, entry on the first 1-point turn, stop 3 points past the zone's near edge (the owner's
normal stop), STRATEGY.md targets. Most of it came from two trades. This runs the same
version, and every other exit style, on the MES trades already downloaded for step 2
(data/raw/trades_rth, regular hours, late Apr to late Oct 2026), with zones from the MES
1-minute bars (research.zones, same settings). No order book: rule A needs none.

Writes research/results/journal/zone_2026_tests.csv and zone_2026_bot.csv.
Run from the repo root:   python -m research.zone_2026
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd

from research.absorption_study import session_files
from research.bot_week_study import CONTRACTS, ES_POINT, FLAT_ET, RULES, bot_trades, et
from research.exit_study import day_context, one_exit
from research.journal_study import OUT
from research.level_study import prepare
from research.level_test_book import TestReader, find_tests
from research.market_data import epoch_seconds, load_trades
from research.owner_rules import DayTrades
from research.settings import load_settings
from research.zone_book_study import zone_levels, zone_of
from research.zones import all_zones


def main() -> None:
    s = load_settings()
    fb, sessions, _ = prepare(s)
    zones, _ = all_zones(fb, s)
    exits = s["book_study"]["exits"]
    cost = CONTRACTS * s["backtest"]["commission_per_contract_round_trip"]
    rows = []
    for day, contract, f in session_files():
        if day not in sessions.index or not sessions.loc[day, "use"] or sessions.loc[day, "contract"] != contract:
            continue
        d_str, off = day.date().isoformat(), int(sessions.loc[day, "offset_ticks"])
        tr = load_trades(f, s)
        trades = pd.DataFrame(dict(t=epoch_seconds(tr["ts"]), price=tr["price"].to_numpy().astype(np.int64),
                                   size=tr["size"].to_numpy(), side=tr["side"].to_numpy()))
        dt = DayTrades(trades=trades)
        start, end, flat = et(d_str, "09:30"), et(d_str, "15:30"), et(d_str, FLAT_ET)
        tests = find_tests(SimpleNamespace(trades=trades), zone_levels(zones, off, start, end), start, end)
        if tests.empty:
            continue
        ctx = day_context(None, d_str, s, trades)
        for x in tests.itertuples():
            d = int(x.direction)
            k, how = TestReader.turn_trade(dt, x.time, d, int(x.edge))
            row = dict(day=d_str, contract=contract, time=x.time, direction=d, edge=x.edge, turn=how,
                       **zone_of(x, zones, off))
            if k is not None:
                i0 = int(np.searchsorted(dt.t, x.time, "left"))
                seg = dt.px[i0:k + 1]
                r = SimpleNamespace(direction=d, time=x.time, edge=int(x.edge), entry_time=float(dt.t[k]),
                                    entry_price=int(dt.px[k]), test_extreme=int(seg.min() if d == 1 else seg.max()),
                                    lo=int(x.edge), hi=int(x.edge))                # the level = the near edge
                row.update(entry_time=r.entry_time, entry_price=r.entry_price)
                for ex in exits:
                    res = one_exit(ctx, r, ex, s, flat)
                    row[f"taken | {ex['name']}"] = res.get("taken", False)
                    row[f"points | {ex['name']}"] = res.get("points", np.nan)
                    row[f"exit_time | {ex['name']}"] = res.get("exit_time", np.nan)
            rows.append(row)
        print(f"{d_str} {contract}: {len(tests)} zone tests", flush=True)
    x = pd.DataFrame(rows)
    x.to_csv(OUT / "zone_2026_tests.csv", index=False)

    out = []
    for ex in exits:
        n = ex["name"]
        te = x.copy()
        te["points"], te["exit_time"] = te[f"points | {n}"], te[f"exit_time | {n}"]
        te.loc[~te[f"taken | {n}"].fillna(False).astype(bool), "turn"] = "skipped by the exit rule"
        b = bot_trades(te, RULES["A every level test"], False)
        usd = b["points"] * ES_POINT - cost
        month = pd.to_datetime(b["day"]).dt.to_period("M").astype(str)
        best2 = usd.nlargest(2).sum() if len(usd) else 0.0
        out.append(dict(exit=n, trades=len(b), win_rate=(b["points"] > 0).mean() if len(b) else np.nan,
                        net_usd=usd.sum(), usd_per_trade=usd.mean() if len(b) else np.nan,
                        without_best_2=usd.sum() - best2,
                        months_up=int((usd.groupby(month).sum() > 0).sum()), months=int(month.nunique())))
    bot = pd.DataFrame(out)
    bot.to_csv(OUT / "zone_2026_bot.csv", index=False)
    pd.set_option("display.width", 250)
    print(f"{x['day'].nunique()} days, {len(x)} zone tests, {int((x['turn'] == 'turned').sum())} turned")
    print(bot.round(2).to_string())


if __name__ == "__main__":
    main()
