"""Out-of-sample check on 2026: does the weaker retest still beat the other level tests?

The 2024 ES study (research.owner_rules) found that a retest of a marked level with a
weaker push into it (at most 80% of the aggressive volume of the previous attempt, in
the 2 minutes before arrival) won 34% with a 2-point stop / 4-point target, against
15% for retests that were not weaker. That uses trades only, so it can be checked on
the MES trades already downloaded for step 2 (data/raw/trades_rth, regular hours,
late Apr to late Oct 2026) at no cost.

Same pieces as 2024: marked levels (last 5 sessions' uncovered POCs and live 15-minute
supply/demand zones, here from the MES 1-minute bars, shifted onto the session's
contract), tests between 09:30 and 15:30 ET, entry on the first 1-point turn, fill at
the trigger trade + 1 tick, 2-point stop / 4-point target (and the owner's next-POC
target with a 3-point stop), flat at 15:59, $12.40 costs for 10 MES. Plus random
entries on the same days with the same exits.

Writes research/results/journal/retest_2026_tests.csv and retest_2026_summary.csv.
Run from the repo root:   python -m research.retest_2026
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd

from research.absorption_study import session_files
from research.bot_week_study import CONTRACTS, ES_POINT, FLAT_ET, SLIP_TICKS, et
from research.journal_study import OUT
from research.level_study import prepare
from research.level_test_book import TestReader, find_tests, marked_levels
from research.market_data import epoch_seconds, load_trades
from research.owner_rules import R, DayTrades, features, tk, trade
from research.settings import load_settings
from research.supply_demand import find_zones, make_candles, zone_lifetimes

RANDOM_PER_DAY = 100


def mes_context(s: dict):
    """MES sessions (POCs, value areas, roll offsets) and 15-minute zones, from the level-study bars."""
    fb, sessions, _ = prepare(s)
    bot = s["bot"]
    candles = make_candles(fb, 15)
    zones = find_zones(candles, bot["sd_base_min_candles"], bot["sd_departure_min_ratio"], bot["sd_zone_drawing"])
    zones = zone_lifetimes(zones, candles, fb, bot["sd_chop_invalid_minutes"],
                           bot["sd_lookback_months"] * s["level_study"]["sessions_per_month"], list(sessions.index))
    return sessions, zones


def won_2_4(dt: DayTrades, t0: float, fill: int, d: int, flat: float) -> bool:
    i, j = np.searchsorted(dt.t, [t0, flat], "right")
    m = (dt.px[i:j] - fill) * d
    hs = np.nonzero(m <= -tk(2))[0]
    ht = np.nonzero(m >= tk(4) + 1)[0]
    return (ht[0] if len(ht) else np.inf) < (hs[0] if len(hs) else np.inf)


def main() -> None:
    s = load_settings()
    sessions, zones = mes_context(s)
    lookback = s["bot"]["recent_poc_lookback_sessions"]
    cost = CONTRACTS * s["backtest"]["commission_per_contract_round_trip"]
    rng = np.random.default_rng(s["level_study"]["random_seed"])
    rows, rand = [], []
    for day, contract, f in session_files():
        if day not in sessions.index or not sessions.loc[day, "use"] or sessions.loc[day, "contract"] != contract:
            continue
        d_str = day.date().isoformat()
        off = int(sessions.loc[day, "offset_ticks"])
        tr = load_trades(f, s)
        trades = pd.DataFrame(dict(t=epoch_seconds(tr["ts"]), price=tr["price"].to_numpy().astype(np.int64),
                                   size=tr["size"].to_numpy(), side=tr["side"].to_numpy()))
        dt = DayTrades(trades=trades)
        start, end, flat = et(d_str, "09:30"), et(d_str, "15:30"), et(d_str, FLAT_ET)
        levels = marked_levels(sessions, zones, day, off, start, end, lookback)
        pocs = levels.loc[levels["source"] == "POC", "edge"].to_numpy() if len(levels) else np.array([])
        tests = find_tests(SimpleNamespace(trades=trades), levels, start, end)

        for t0 in np.sort(rng.uniform(start, end, RANDOM_PER_DAY)):            # random entries, same exits
            d = int(rng.choice([-1, 1]))
            i = int(np.searchsorted(dt.t, t0, "right"))
            if i < len(dt.px):
                rand.append(dict(day=d_str, won=won_2_4(dt, dt.t[i], int(dt.px[i]) + d * SLIP_TICKS, d, flat)))
        if tests.empty:
            continue
        for x in tests.itertuples():
            k, how = TestReader.turn_trade(dt, x.time, int(x.direction), int(x.edge))
            if k is None:
                continue
            i0 = int(np.searchsorted(dt.t, x.time, "left"))
            seg = dt.px[i0:k + 1]
            d = int(x.direction)
            earlier = tests[(tests["edge"] == x.edge) & (tests["direction"] == x.direction) & (tests["time"] < x.time)
                            & (tests["time"] >= x.time - R["retest_within_min"] * 60)]
            prev = float(earlier["time"].max()) if len(earlier) else None
            r = SimpleNamespace(direction=d, time=x.time, edge=int(x.edge), entry_time=float(dt.t[k]),
                                entry_price=int(dt.px[k]), test_extreme=int(seg.min() if d == 1 else seg.max()))
            lv_now = marked_levels(sessions, zones, day, off, r.entry_time, r.entry_time, lookback)
            zones_now = lv_now[lv_now["source"] == "zone"] if len(lv_now) else lv_now
            feat = features(dt, r, prev, zones_now)
            rows.append(dict(day=d_str, contract=contract, time=x.time, entry_time=r.entry_time, direction=d,
                             sources=x.sources, edge=x.edge, **feat,
                             pts_2_4=trade(dt, r, pocs, flat, 2, "fixed 4"),
                             pts_poc=trade(dt, r, pocs, flat, R["stop_points"], "next POC")))
        print(f"{d_str} {contract}: {len(levels)} levels, {len(tests)} tests", flush=True)

    x = pd.DataFrame(rows)
    x.to_csv(OUT / "retest_2026_tests.csv", index=False)
    rnd = pd.DataFrame(rand)

    groups = {
        "every test (first 1-point turn)": np.ones(len(x), bool),
        "first test of the level that session": ~x["retest"],
        "retest, push weaker than last time": x["weaker_retest"],
        "retest, push not weaker": x["retest"] & ~x["weaker_retest"],
        "absorption spike": x["spike"],
        "delta divergence": x["divergence"],
        "POC tests": x["sources"] == "POC",
        "zone tests": x["sources"] == "zone",
    }
    out = []
    for name, m in groups.items():
        g = x[np.asarray(m, bool)]
        out.append(dict(group=name, tests=len(g), win_2_4=(g["pts_2_4"] > 0).mean(),
                        usd_per_trade_2_4=g["pts_2_4"].mean() * ES_POINT - cost,
                        win_next_poc=(g["pts_poc"] > 0).mean(),
                        usd_per_trade_next_poc=g["pts_poc"].mean() * ES_POINT - cost))
    out.append(dict(group="random entries, same days", tests=len(rnd), win_2_4=rnd["won"].mean()))
    table = pd.DataFrame(out)

    # 95% range for (weaker - not weaker) win rate, resampling whole days
    w, nw = x[x["weaker_retest"]], x[x["retest"] & ~x["weaker_retest"]]
    days = x["day"].unique()
    diffs = []
    for _ in range(s["level_study"]["bootstrap_samples"]):
        pick = pd.Series(rng.choice(days, len(days)))
        cw = pick.map(w.groupby("day")["pts_2_4"].apply(lambda v: (v > 0).sum())).fillna(0).sum()
        nw_ = pick.map(w.groupby("day").size()).fillna(0).sum()
        cn = pick.map(nw.groupby("day")["pts_2_4"].apply(lambda v: (v > 0).sum())).fillna(0).sum()
        nn = pick.map(nw.groupby("day").size()).fillna(0).sum()
        if nw_ and nn:
            diffs.append(cw / nw_ - cn / nn)
    lo, hi = np.percentile(diffs, [2.5, 97.5])
    table.to_csv(OUT / "retest_2026_summary.csv", index=False)
    pd.set_option("display.width", 250)
    print(table.round(3).to_string())
    print(f"weaker minus not weaker (2/4 win rate): {w['pts_2_4'].gt(0).mean() - nw['pts_2_4'].gt(0).mean():+.1%}, "
          f"95% range {lo:+.1%} to {hi:+.1%}")


if __name__ == "__main__":
    main()
