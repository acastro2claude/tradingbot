"""How close do 1-minute bars get to real trades? (the 4 check days)

For each day with trade data, compares:
  - POC and value area from bars vs from every trade
  - the outcome of the first test of each price (held 6 / broke 3 / neither)
    using the bar-based price path vs the real trade-by-trade path

Run from the repo root:  python -m research.check_bars_vs_trades
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from research.level_study import BARS_FILE, OUT, RAW, SYMBOLOGY_FILE
from research.market_data import load_bars, load_trades
from research.paths import bar_path, trade_path
from research.profiles import bar_volume_at_price, poc, trade_volume_at_price, value_area
from research.settings import load_settings, ticks
from research.touches import outcome, touch_events

DAYS = [("MESM5", "2025-04-03"), ("MESZ5", "2025-10-21"), ("MESH6", "2026-02-24"), ("MESU6", "2026-07-21")]


def first_test_outcomes(p, t, prices, s) -> pd.Series:
    ls = s["level_study"]
    ev = touch_events(p, t, prices, arm=ticks(ls["arm_distance_points"], s),
                      brk=ticks(ls["break_distance_points"], s),
                      horizon=ls["outcome_horizon_minutes"] * 60.0)
    first = ev[ev["test_no"] == 1].set_index("price")
    hold = ticks(ls["headline_hold_distance_points"], s)
    return pd.Series(outcome(first["mfe"], first["broke"], hold), index=first.index)


def main() -> None:
    s = load_settings()
    tick = s["market"]["tick_size"]
    bars = load_bars(BARS_FILE, SYMBOLOGY_FILE, s)
    rows = []
    for contract, day in DAYS:
        tr = load_trades(RAW / f"{contract}_trades_{day}.dbn.zst", s)
        tr = tr[tr["session"] == pd.Timestamp(day)]
        b = bars[(bars["session"] == pd.Timestamp(day)) & (bars["contract"] == contract)]

        tt, tv = trade_volume_at_price(tr["price"], tr["size"])
        bt, bv = bar_volume_at_price(b["low"], b["high"], b["volume"])
        t_poc, b_poc = poc(tt, tv), poc(bt, bv)
        t_va, b_va = value_area(tt, tv), value_area(bt, bv)
        grid = np.arange(min(tt.min(), bt.min()), max(tt.max(), bt.max()) + 1)
        corr = np.corrcoef(pd.Series(tv, tt).reindex(grid, fill_value=0),
                           pd.Series(bv, bt).reindex(grid, fill_value=0))[0, 1]

        tp, tts = trade_path(tr["price"].values, tr["ts"].astype("int64").values / 1e9)
        bp, bts = bar_path(b["open"], b["high"], b["low"], b["close"], b["ts"].astype("int64").values / 1e9)
        prices = np.arange(max(tp.min(), bp.min()), min(tp.max(), bp.max()) + 1)
        ot, ob = first_test_outcomes(tp, tts, prices, s), first_test_outcomes(bp, bts, prices, s)
        both = pd.concat([ot.rename("trades"), ob.rename("bars")], axis=1, join="inner")

        rows.append(dict(
            session=day, contract=contract,
            trade_volume=int(tr["size"].sum()), bar_volume=int(b["volume"].sum()),
            poc_trades=t_poc * tick, poc_bars=b_poc * tick, poc_diff_ticks=b_poc - t_poc,
            va_trades=f"{t_va[0] * tick:.2f}-{t_va[1] * tick:.2f}",
            va_bars=f"{b_va[0] * tick:.2f}-{b_va[1] * tick:.2f}",
            profile_correlation=round(corr, 3),
            prices_tested=len(both),
            same_outcome=round((both["trades"] == both["bars"]).mean(), 3),
            hold_rate_trades=round((both["trades"] == "hold").mean(), 3),
            hold_rate_bars=round((both["bars"] == "hold").mean(), 3),
        ))
    out = pd.DataFrame(rows)
    print(out.to_string(index=False))
    out.to_csv(OUT / "check_bars_vs_trades.csv", index=False)


if __name__ == "__main__":
    main()
