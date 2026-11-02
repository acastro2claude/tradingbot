"""Plan an order-book purchase for more replay clips: MES 2026 days on disk, priced per day (free quotes).

For every MES regular session with trades on disk (Apr-Oct 2026): the zone tests that a replay clip
would show (10:00-15:30 ET, after the owner's filters: no news spike window, no Fed day), and the
price of that day's full order book (MBO) from 00:00 UTC (Databento's daily snapshot) to 16:00 ET.
Days are picked month by month in turn (so every month is covered), each time the cheapest per zone
test in that month, until the budget ([replay_2026] budget_usd) is used. Nothing is downloaded.

Writes research/results/journal/replay2026_plan.csv. Run:   python -m research.replay2026_plan
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd

from research.absorption_study import session_files
from research.bot_week_study import et
from research.databento_http import get_cost
from research.entry_read import filters
from research.journal_study import OUT
from research.level_study import prepare
from research.level_test_book import find_tests
from research.market_data import epoch_seconds, load_trades
from research.settings import load_settings
from research.zone_book_study import zone_levels
from research.zones import all_zones


def main() -> None:
    s = load_settings()
    P = s["replay_2026"]
    fb, sessions, _ = prepare(s)
    zones, _ = all_zones(fb, s)
    loc = fb["ts"].dt.tz_convert("America/New_York")
    vol = fb.groupby([loc.dt.date.astype(str), loc.dt.strftime("%H:%M")])["volume"].sum()
    rows = []
    for day, contract, f in session_files():
        if day not in sessions.index or not sessions.loc[day, "use"] or sessions.loc[day, "contract"] != contract:
            continue
        d_str, off = day.date().isoformat(), int(sessions.loc[day, "offset_ticks"])
        tr = load_trades(f, s)
        trades = pd.DataFrame(dict(t=epoch_seconds(tr["ts"]), price=tr["price"].to_numpy().astype(np.int64),
                                   size=tr["size"].to_numpy(), side=tr["side"].to_numpy()))
        start, end = et(d_str, "09:30"), et(d_str, "15:30")
        mv = vol.loc[d_str] if d_str in vol.index.get_level_values(0) else pd.Series(dtype=float)
        tests = find_tests(SimpleNamespace(trades=trades), zone_levels(zones, off, start, end), start, end)
        n = 0 if tests.empty else sum(filters(d_str, x.time, mv, 0.0, 0.0) is None for x in tests.itertuples())
        b0 = f"{d_str}T{P['book_start_utc']}:00Z"
        b1 = pd.Timestamp(et(d_str, P["book_end_et"]), unit="s", tz="UTC").strftime("%Y-%m-%dT%H:%M:%SZ")
        cost = get_cost(contract, "mbo", b0, b1, "raw_symbol") if n else np.nan
        rows.append(dict(day=d_str, contract=contract, zone_tests=n, book_start=b0, book_end=b1, cost=cost))
        print(d_str, contract, n, cost, flush=True)
    x = pd.DataFrame(rows)
    x["month"] = x["day"].str[:7]
    x["per_test"] = x["cost"] / x["zone_tests"].replace(0, np.nan)
    x["pick"] = False
    left = P["budget_usd"]
    queues = {m: g.dropna(subset=["per_test"]).sort_values("per_test").index.tolist() for m, g in x.groupby("month")}
    while any(queues.values()):
        took = False
        for m in sorted(queues):
            while queues[m]:
                i = queues[m].pop(0)
                if x.loc[i, "cost"] <= left:
                    x.loc[i, "pick"], left, took = True, left - x.loc[i, "cost"], True
                    break
        if not took:
            break
    x.to_csv(OUT / "replay2026_plan.csv", index=False)
    p = x[x["pick"]]
    print(f"picked {len(p)} days, {int(p['zone_tests'].sum())} zone tests, ${p['cost'].sum():.2f}")
    print(p.groupby("month").agg(days=("day", "size"), tests=("zone_tests", "sum"), cost=("cost", "sum")).round(2))
    print("all days:", len(x), "tests", int(x["zone_tests"].sum()), "cost of all", round(x["cost"].sum(), 2))


if __name__ == "__main__":
    main()
