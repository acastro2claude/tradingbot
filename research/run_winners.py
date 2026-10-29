"""The owner's risk structure at the zones: 4-point stop, let winners run to 10-12 points.

Owner (Oct 2026): "a 4 point stop loss, but let winners run to about 10-12 points depending
on market conditions (mostly meaning delta is heavily continuing in our direction, and
aggressive buyers/sellers are still moving the same direction)." Settings: [run_winners].

Exits, on every trade (tick by tick, using each trade's buyer/seller side):
  4/10 and 4/12   fixed stop and target from the fill
  4 -> 10 or 12   stop 4; at +10, if delta over the last minute is our way and our side's
                  aggressive volume over that minute is at least the other side's, hold for
                  +12 with the stop moved up to +8 (proposed); otherwise take the 10
Targets fill when price trades 1 tick through; stops and the 15:59 close pay 1 tick.

Entries: the zone bot's entries (first 1-point turn at a zone test, one trade at a time, up
to 3 a day) on
  MES Apr-Oct 2026 (116 days, research/results/journal/zone_2026_tests.csv), and
  ES, the 23 order-book days of 2024 (zone_book_tests.csv),
against random entries on the same days with the same exits. Money: 10 MES = 1 ES ($50 a
point), $12.40 costs per trade.

Writes research/results/journal/run_winners_summary.csv. Run:   python -m research.run_winners
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from research.absorption_study import session_files
from research.bot_week_study import CONTRACTS, ES_POINT, FLAT_ET, MAX_TRADES, SLIP_TICKS, et
from research.journal_study import OUT
from research.market_data import epoch_seconds, load_trades
from research.owner_rules import DayTrades
from research.settings import load_settings

S = load_settings()
W = S["run_winners"]
TICK = S["market"]["tick_size"]
tk = lambda pts: int(round(pts / TICK))  # noqa: E731


def exit_trade(dt: DayTrades, t_in: float, fill: int, d: int, flat: float, mode: str) -> tuple[float, float]:
    """Ticks per contract from the fill and the exit time."""
    i, j = int(np.searchsorted(dt.t, t_in, "right")), int(np.searchsorted(dt.t, flat, "right"))
    if j <= i:
        return 0.0, t_in
    px, t = dt.px[i:j], dt.t[i:j]

    def first(mask):
        k = np.flatnonzero(mask)
        return int(k[0]) if len(k) else len(mask)

    stop_t = tk(W["stop_points"])
    t1 = tk(W["target_points"] if mode != "4/12" else W["extended_target_points"])
    ks = first((px - (fill - d * stop_t)) * d <= 0)
    kt = first((px - (fill + d * t1)) * d >= 1)
    if ks == kt == len(px):
        return float((px[-1] - fill) * d - SLIP_TICKS), float(t[-1])
    if ks <= kt:
        return float(-stop_t - SLIP_TICKS), float(t[ks])
    if mode != "dynamic":
        return float(t1), float(t[kt])
    tt = t[kt]                                                   # at +10: is delta still going our way?
    a, b = dt.idx(tt - W["delta_window_seconds"], tt)
    delta = (dt.cvd[b - 1] - dt.cvd[a - 1 if a > 0 else 0]) * d if b > a else 0
    ours = dt.volume(tt - W["delta_window_seconds"], tt, buyers=(d == 1))
    theirs = dt.volume(tt - W["delta_window_seconds"], tt, buyers=(d == -1))
    if not (delta > 0 and ours >= W["aggressor_ratio"] * theirs):
        return float(t1), float(tt)
    px2, t2 = px[kt:], t[kt:]
    lock, t_ext = tk(W["lock_points_when_extended"]), tk(W["extended_target_points"])
    ks2 = first((px2 - (fill + d * lock)) * d <= 0)
    kt2 = first((px2 - (fill + d * t_ext)) * d >= 1)
    if ks2 == kt2 == len(px2):
        return float((px2[-1] - fill) * d - SLIP_TICKS), float(t2[-1])
    if ks2 <= kt2:
        return float(lock - SLIP_TICKS), float(t2[ks2])
    return float(t_ext), float(t2[kt2])


MODES = ["4/10", "4/12", "dynamic"]


def bot_and_random(entries: pd.DataFrame, day_trades, rng) -> pd.DataFrame:
    """entries: day, entry_time, entry_price, direction (turned zone tests). One position at a time, up to 3 a day."""
    rows = []
    for day, g in entries.sort_values("entry_time").groupby("day"):
        dt = day_trades(day)
        if dt is None:
            continue
        flat = et(day, FLAT_ET)
        for mode in MODES:
            busy, n = -np.inf, 0
            for r in g.itertuples():
                if n >= MAX_TRADES or r.entry_time < busy:
                    continue
                fill = int(r.entry_price) + int(r.direction) * SLIP_TICKS
                pts, t_out = exit_trade(dt, r.entry_time, fill, int(r.direction), flat, mode)
                rows.append(dict(who="zone bot", day=day, exit=mode, points=pts * TICK))
                busy, n = t_out, n + 1
        for t0 in rng.uniform(et(day, "09:30"), et(day, "15:30"), W["random_per_day"]):
            k = int(np.searchsorted(dt.t, t0, "right"))
            if k >= len(dt.t):
                continue
            d = int(rng.choice([-1, 1]))
            fill = int(dt.px[k]) + d * SLIP_TICKS
            for mode in MODES:
                pts, _ = exit_trade(dt, float(dt.t[k]), fill, d, flat, mode)
                rows.append(dict(who="random entries", day=day, exit=mode, points=pts * TICK))
    return pd.DataFrame(rows)


def mes_2026(rng) -> pd.DataFrame:
    x = pd.read_csv(OUT / "zone_2026_tests.csv")
    x = x[x["turn"] == "turned"]
    files = {d.date().isoformat(): f for d, _, f in session_files()}

    def load(day):
        if day not in files:
            return None
        tr = load_trades(files[day], S)
        return DayTrades(trades=pd.DataFrame(dict(t=epoch_seconds(tr["ts"]), price=tr["price"].to_numpy().astype(np.int64),
                                                  size=tr["size"].to_numpy(), side=tr["side"].to_numpy())))
    return bot_and_random(x, load, rng).assign(market="MES Apr-Oct 2026")


def es_2024(rng) -> pd.DataFrame:
    x = pd.read_csv(OUT / "zone_book_tests.csv")
    x = x[x["turn"] == "turned"]
    es = x.groupby("day")["es"].first()
    return bot_and_random(x, lambda day: DayTrades(es[day], day), rng).assign(market="ES 23 days 2024")


def main() -> None:
    rng = np.random.default_rng(S["level_study"]["random_seed"])
    cost = CONTRACTS * S["backtest"]["commission_per_contract_round_trip"]
    res = pd.concat([mes_2026(rng), es_2024(rng)], ignore_index=True)
    res["usd"] = res["points"] * ES_POINT - cost
    res["month"] = pd.to_datetime(res["day"]).dt.to_period("M").astype(str)
    rows = []
    for (market, who, ex), g in res.groupby(["market", "who", "exit"], sort=False):
        w, lo = g[g["points"] > 0], g[g["points"] <= 0]
        m = g.groupby("month")["usd"].sum()
        rows.append(dict(market=market, who=who, exit=ex, trades=len(g), win_rate=(g["points"] > 0).mean(),
                         avg_win_pts=w["points"].mean(), avg_loss_pts=lo["points"].mean(),
                         usd_per_trade=g["usd"].mean(), net_usd=g["usd"].sum(),
                         without_best_2=g["usd"].sum() - g["usd"].nlargest(2).sum(),
                         months_up=f"{int((m > 0).sum())} of {len(m)}"))
    table = pd.DataFrame(rows)
    table.to_csv(OUT / "run_winners_summary.csv", index=False)
    pd.set_option("display.width", 250)
    print(table.round(2).to_string())


if __name__ == "__main__":
    main()
