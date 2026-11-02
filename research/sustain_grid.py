"""Short bursts of sustained volume our way at a zone test: a grid of settings, replay clips and 2026.

Owner (Nov 2026): the volume our way has to "come in and sustain" for "a couple of seconds, maybe 5
at the most"; try a few settings for how aggressive it must be. Settings: [sustain_grid].

At each zone test (long at demand; supply is the mirror), second by second for 5 minutes:
  - a stretch of 2, 3 or 5 seconds where buyers' aggressive volume is at least 1.5x, 2x or 3x the
    sellers' and at least 1x, 2x or 3x the day's normal aggressive volume for that many seconds,
    with no new low inside it and price at its end not below its start
  - entry at the end of the first such stretch (last price + 1 tick); nothing if price gets 2 points
    past the zone's far edge first
Exits: stop-market 4.5 points, target 10, 5 MES. Filters: the owner's (10:00, news spikes, Fed days).
  - replay clips: the owner's 110 careful decisions (research.entry_read), agreement and money
  - MES Apr-Oct 2026 (unseen): every zone test, one trade at a time, -$500 day stop; random entries
    as the baseline (research.entry_read_2026)
With 27 combinations, the best ones look better than they are; one counts only if it holds on both.
Writes research/results/journal/sustain_grid.csv. Run:   python -m research.sustain_grid
"""

from __future__ import annotations

from itertools import product
from types import SimpleNamespace

import numpy as np
import pandas as pd

from research.absorption_study import session_files
from research.bot_week_study import FLAT_ET, SLIP_TICKS, et
from research.entry_read import E, day_trades_df, filters
from research.entry_read_2026 import day_rules
from research.journal_study import OUT
from research.level_study import prepare
from research.level_test_book import find_tests
from research.market_data import epoch_seconds, load_trades
from research.owner_rules import DayTrades
from research.replay_score import TICK, fixed_exit_t
from research.settings import load_settings
from research.zone_book_study import zone_levels
from research.zones import all_zones

S = load_settings()
G = S["sustain_grid"]
COMBOS = list(product(G["seconds"], G["ratios"], G["surges"]))


def normal_per_second(dt: DayTrades, day: str) -> float:
    a, b = np.searchsorted(dt.t, [et(day, G["normal_from_et"]), et(day, G["normal_to_et"])])
    span = et(day, G["normal_to_et"]) - et(day, G["normal_from_et"])
    return float((dt.buy[a:b].sum() + dt.sell[a:b].sum()) / 2 / span) if b > a else np.nan


def per_second(dt: DayTrades, T: float, d: int, far: int) -> dict:
    """1-second arrays for the 5 minutes after the test, in the trade's direction."""
    n = int(E["window_seconds"])
    edges = np.searchsorted(dt.t, T + np.arange(n + 1))
    ours = np.add.reduceat(dt.buy if d == 1 else dt.sell, edges[:-1]) if len(dt.t) else np.zeros(n)
    theirs = np.add.reduceat(dt.sell if d == 1 else dt.buy, edges[:-1]) if len(dt.t) else np.zeros(n)
    empty = edges[1:] == edges[:-1]
    ours[empty], theirs[empty] = 0, 0
    y = dt.px * d
    low = np.full(n, np.nan)
    close = np.full(n, np.nan)
    for k in np.flatnonzero(~empty):
        seg = y[edges[k]:edges[k + 1]]
        low[k], close[k] = seg.min(), seg[-1]
    close = pd.Series(close).ffill().to_numpy()
    low = np.where(np.isnan(low), close, low)
    run_min = np.fmin.accumulate(np.where(np.isnan(low), np.inf, low))
    brk = np.flatnonzero(run_min <= far * d - int(round(E["break_points"] / TICK)))
    return dict(ours=ours, theirs=theirs, low=low, close=close, run_min=run_min, edges=edges,
                stop_at=int(brk[0]) if len(brk) else n)


def first_entry(a: dict, secs: int, ratio: float, surge: float, normal: float) -> int | None:
    """Index of the second at whose end the stretch completes, or None."""
    o = np.convolve(a["ours"], np.ones(secs, int), "valid")
    t = np.convolve(a["theirs"], np.ones(secs, int), "valid")
    end = np.arange(secs - 1, len(a["ours"]))
    start = end - secs + 1
    before = np.concatenate([[np.inf], a["run_min"]])[start]           # the low before the stretch began
    no_new_low = np.array([a["low"][s:e + 1].min() for s, e in zip(start, end)]) >= np.minimum(before, np.inf)
    not_lower = a["close"][end] >= np.concatenate([[a["close"][0]], a["close"]])[start]
    ok = (o >= ratio * np.maximum(t, 1)) & (o >= surge * normal * secs) & no_new_low & not_lower & (end < a["stop_at"])
    k = np.flatnonzero(ok)
    return int(end[k[0]]) if len(k) else None


def entries_for_test(dt, T, d, far, normal, flat) -> dict:
    """Points for every combination at one test (NaN if no entry)."""
    a = per_second(dt, T, d, far)
    out = {}
    for secs, ratio, surge in COMBOS:
        k = first_entry(a, secs, ratio, surge, normal)
        if k is None:
            continue
        t_in = T + k + 1
        px_in = int(a["close"][k]) * d
        pts, t_out = fixed_exit_t(dt, t_in, px_in + d * SLIP_TICKS, d, flat, S["bot"]["stop_points"],
                                  S["run_winners"]["target_points"])
        out[(secs, ratio, surge)] = (t_in, t_out, pts * TICK)
    return out


def usd(points: float) -> float:
    c = S["bot"]["contracts_per_trade"]
    return points * S["market"]["point_value_usd"] * c - S["backtest"]["commission_per_contract_round_trip"] * c


def replay() -> pd.DataFrame:
    x = pd.read_csv(OUT / "entry_read_replay.csv")
    t = pd.read_csv(OUT / "replay_clip_ids.csv").merge(pd.read_csv(OUT / "replay_tests.csv"), on="id")
    x = x.merge(t[["public_id", "contract", "time", "edge", "lo", "hi"]], left_on="clip", right_on="public_id")
    x = x[x["careful"]]
    rows = []
    for (m, c, day), g in x.groupby(["market", "contract", "day"]):
        dt = DayTrades(trades=day_trades_df(m, c, day))
        normal, flat = normal_per_second(dt, day), et(day, FLAT_ET)
        for r in g.itertuples():
            far = int(r.lo) if r.zone_dir == 1 else int(r.hi)
            got = entries_for_test(dt, r.time, r.zone_dir, far, normal, flat) if pd.isna(r.filter) else {}
            for combo in COMBOS:
                rows.append(dict(clip=r.clip, owner_takes=r.owner_takes, combo=combo,
                                 takes=combo in got, points=got[combo][2] if combo in got else np.nan))
    y = pd.DataFrame(rows)
    out = []
    for combo, g in y.groupby("combo"):
        p = g.loc[g["takes"], "points"]
        out.append(dict(combo=combo, replay_trades=int(g["takes"].sum()),
                        replay_owner_matched=int((g["takes"] & g["owner_takes"]).sum()),
                        replay_agreement=(g["takes"] == g["owner_takes"]).mean(),
                        replay_win_rate=(p > 0).mean(), replay_usd_per_trade=usd(p).mean()))
    return pd.DataFrame(out)


def mes_2026() -> tuple[pd.DataFrame, float]:
    fb, sessions, _ = prepare(S)
    zones, _ = all_zones(fb, S)
    loc = fb["ts"].dt.tz_convert("America/New_York")
    vol = fb.groupby([loc.dt.date.astype(str), loc.dt.strftime("%H:%M")])["volume"].sum()
    rows = []
    for day, contract, f in session_files():
        if day not in sessions.index or not sessions.loc[day, "use"] or sessions.loc[day, "contract"] != contract:
            continue
        d_str, off = day.date().isoformat(), int(sessions.loc[day, "offset_ticks"])
        tr = load_trades(f, S)
        trades = pd.DataFrame(dict(t=epoch_seconds(tr["ts"]), price=tr["price"].to_numpy().astype(np.int64),
                                   size=tr["size"].to_numpy(), side=tr["side"].to_numpy()))
        dt = DayTrades(trades=trades)
        normal, flat = normal_per_second(dt, d_str), et(d_str, FLAT_ET)
        start, end = et(d_str, "09:30"), et(d_str, "15:30")
        mv = vol.loc[d_str] if d_str in vol.index.get_level_values(0) else pd.Series(dtype=float)
        tests = find_tests(SimpleNamespace(trades=trades), zone_levels(zones, off, start, end), start, end)
        for x in (tests.itertuples() if not tests.empty else []):
            if filters(d_str, x.time, mv, 0.0, 0.0) is not None:
                continue
            d = int(x.direction)
            far = int(x.lo) if d == 1 else int(x.hi)
            for combo, (t_in, t_out, pts) in entries_for_test(dt, x.time, d, far, normal, flat).items():
                rows.append(dict(combo=combo, day=d_str, entry_time=t_in, exit_time=t_out, points=pts, usd=usd(pts)))
        print(d_str, flush=True)
    y = pd.DataFrame(rows)
    out = []
    for combo, g in y.groupby("combo"):
        v = day_rules(g, S)
        half = pd.to_datetime(v["day"]) < pd.Timestamp("2026-07-15")
        out.append(dict(combo=combo, y2026_trades=len(v), y2026_win_rate=(v["points"] > 0).mean(),
                        y2026_usd_per_trade=v["usd"].mean(), y2026_net=v["usd"].sum(),
                        y2026_first_half=v.loc[half, "usd"].mean(), y2026_second_half=v.loc[~half, "usd"].mean()))
    rand = pd.read_csv(OUT / "entry_read_2026_summary.csv")
    r0 = rand[(rand["rule"] == "random entries") & (rand.get("exit", "4.5/10") == "4.5/10")]
    base = float(r0["usd_per_trade"].iloc[0])
    return pd.DataFrame(out), base


def main() -> None:
    r = replay()
    m, base = mes_2026()
    t = r.merge(m, on="combo", how="outer")
    t[["seconds", "ratio", "surge"]] = pd.DataFrame(t["combo"].tolist(), index=t.index)
    t = t.drop(columns="combo")[["seconds", "ratio", "surge"] + [c for c in t.columns if c not in ("seconds", "ratio", "surge", "combo")]]
    t.to_csv(OUT / "sustain_grid.csv", index=False)
    pd.set_option("display.width", 300)
    pd.set_option("display.max_columns", 30)
    print("random entries 2026, per trade:", round(base, 2))
    print(t.round(2).to_string())


if __name__ == "__main__":
    main()
