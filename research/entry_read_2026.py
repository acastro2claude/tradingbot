"""Out-of-sample check of the coded entry read: MES regular sessions Apr-Oct 2026 (tick data on disk).

The read's versions were built and tuned on the owner's replay clips (2024 days and one 2025 day).
Here they run at every zone test on days they never saw:
  - zones from the MES 1-minute bars (research.zones, the owner-approved rules), tests between 09:30
    and 15:30 (research.level_test_book.find_tests)
  - the owner's filters (research.entry_read.filters): no entries before 10:00, none within 30
    minutes after a news volume spike, no Fed days
  - entries: version 4 "sustained volume our way" (the rule picked on the replay clips), version 2
    (absorption / CVD signals), and the old bot entry (first 1-point turn) for comparison
  - exits: the owner's settings: stop-market 4.5 points, target 10 points ([bot] stop_points,
    [run_winners] target_points); 1 tick slippage on stops and entries; flat at 15:59
  - the day: one trade at a time, no new trades once the day's closed trades reach -$500 ([bot])
  - money: 5 MES ([bot] contracts_per_trade), $1.24 a contract
  - random entries (50 a day, 10:00-15:30, random side, same exits) as the no-edge baseline
Writes research/results/journal/entry_read_2026.csv (one row per trade) and entry_read_2026_summary.csv.
Run from the repo root:   python -m research.entry_read_2026
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd

from research.absorption import find_bursts
from research.absorption_study import session_files
from research.bot_week_study import FLAT_ET, SLIP_TICKS, et
from research.entry_read import E, filters, read_sustained, read_test
from research.journal_study import OUT
from research.level_study import prepare
from research.level_test_book import TestReader, find_tests
from research.market_data import epoch_seconds, load_trades
from research.owner_rules import DayTrades
from research.replay_score import TICK, fixed_exit_t
from research.settings import load_settings
from research.zone_book_study import zone_levels
from research.zones import all_zones


def day_rules(trades: pd.DataFrame, s: dict) -> pd.DataFrame:
    """One trade at a time; no new trades once the day's closed trades reach the daily loss limit."""
    keep = []
    for _, g in trades.sort_values("entry_time").groupby("day"):
        busy, pnl = -np.inf, 0.0
        for r in g.itertuples():
            if r.entry_time < busy or pnl <= -s["bot"]["daily_loss_limit_usd"]:
                continue
            keep.append(r.Index)
            busy, pnl = r.exit_time, pnl + r.usd
    return trades.loc[keep]


def main() -> None:
    s = load_settings()
    rng = np.random.default_rng(s["level_study"]["random_seed"])
    contracts = s["bot"]["contracts_per_trade"]
    usd = lambda pts: pts * s["market"]["point_value_usd"] * contracts - s["backtest"]["commission_per_contract_round_trip"] * contracts  # noqa: E731
    stop, target = s["bot"]["stop_points"], s["run_winners"]["target_points"]
    fb, sessions, _ = prepare(s)
    zones, _ = all_zones(fb, s)
    loc = fb["ts"].dt.tz_convert("America/New_York")
    vol = fb.groupby([loc.dt.date.astype(str), loc.dt.strftime("%H:%M")])["volume"].sum()
    R = s["replay"]
    rows, rand = [], []
    for day, contract, f in session_files():
        if day not in sessions.index or not sessions.loc[day, "use"] or sessions.loc[day, "contract"] != contract:
            continue
        d_str, off = day.date().isoformat(), int(sessions.loc[day, "offset_ticks"])
        tr = load_trades(f, s)
        trades = pd.DataFrame(dict(t=epoch_seconds(tr["ts"]), price=tr["price"].to_numpy().astype(np.int64),
                                   size=tr["size"].to_numpy(), side=tr["side"].to_numpy()))
        dt = DayTrades(trades=trades)
        bursts = find_bursts(trades["t"].to_numpy(), trades["price"].to_numpy(), trades["size"].to_numpy(),
                             trades["side"].to_numpy(), R["burst_seconds"], R["burst_min_mes"])
        bursts["direction"] = np.where(bursts["side"] == "B", -1, 1)
        start, end, flat = et(d_str, "09:30"), et(d_str, "15:30"), et(d_str, FLAT_ET)
        mv = vol.loc[d_str] if d_str in vol.index.get_level_values(0) else pd.Series(dtype=float)
        tests = find_tests(SimpleNamespace(trades=trades), zone_levels(zones, off, start, end), start, end)
        for x in (tests.itertuples() if not tests.empty else []):
            d = int(x.direction)
            if filters(d_str, x.time, mv, 0.0, 0.0) is not None:
                continue
            far = int(x.lo) if d == 1 else int(x.hi)
            entries = {}
            su = read_sustained(dt, x.time, d, int(x.edge), far)
            if su:
                entries["v4 sustained volume"] = (su["entry_time"], su["entry_y"] * d)
            v2 = read_test(dt, bursts, x.time, d, int(x.edge), far)
            if v2:
                entries["v2 absorption / CVD"] = (v2["entry_time"], v2["entry_y"] * d)
            k, _ = TestReader.turn_trade(dt, x.time, d, int(x.edge))
            if k is not None:
                entries["old bot: first turn"] = (float(dt.t[k]), int(dt.px[k]))
            for rule, (t_in, px_in) in entries.items():
                pts, t_out = fixed_exit_t(dt, t_in, px_in + d * SLIP_TICKS, d, flat, stop, target)
                rows.append(dict(rule=rule, day=d_str, entry_time=t_in, exit_time=t_out, direction=d,
                                 points=pts * TICK, usd=usd(pts * TICK)))
        for t0 in rng.uniform(et(d_str, "10:00"), end, 50):
            i = int(np.searchsorted(dt.t, t0, "right"))
            if i >= len(dt.t):
                continue
            d = int(rng.choice([-1, 1]))
            pts, _ = fixed_exit_t(dt, float(dt.t[i]), int(dt.px[i]) + d * SLIP_TICKS, d, flat, stop, target)
            rand.append(dict(day=d_str, points=pts * TICK, usd=usd(pts * TICK)))
        print(d_str, len(tests), "tests", flush=True)
    x = pd.DataFrame(rows)
    x.to_csv(OUT / "entry_read_2026.csv", index=False)
    rd = pd.DataFrame(rand)
    out = []
    for rule, g in x.groupby("rule"):
        for rules_name, v in (("every signal", g), ("one at a time, -$500 day stop", day_rules(g, s))):
            m = v.groupby(pd.to_datetime(v["day"]).dt.to_period("M"))["usd"].sum()
            out.append(dict(rule=rule, day_rules=rules_name, trades=len(v), days=v["day"].nunique(),
                            win_rate=(v["points"] > 0).mean(), usd_per_trade=v["usd"].mean(), net_usd=v["usd"].sum(),
                            without_best_2=v["usd"].sum() - v["usd"].nlargest(2).sum(),
                            months_up=f"{int((m > 0).sum())} of {len(m)}"))
    out.append(dict(rule="random entries", day_rules="every entry", trades=len(rd), days=rd["day"].nunique(),
                    win_rate=(rd["points"] > 0).mean(), usd_per_trade=rd["usd"].mean()))
    t = pd.DataFrame(out)
    t.to_csv(OUT / "entry_read_2026_summary.csv", index=False)
    pd.set_option("display.width", 250)
    print(t.round(2).to_string())


if __name__ == "__main__":
    main()
