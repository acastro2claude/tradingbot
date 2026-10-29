"""The zone bot on every day with charts, Feb 2024 - Oct 2026, split by year, quarter and type of day.

The owner (Oct 2026): "each day is different. If we keep comparing changes against the same
days we are only trying to optimize for certain days, and not trying to generalize the
strategy in ALL conditions." So this runs on every regular session we have 1-minute candles
for (no new data):
  ES 1-minute bars, Feb 2024 - Mar 2025 (zones from the ES bars)
  MES 1-minute bars, Apr 2025 - Oct 2026 (zones from the MES bars)
2024 is where the zone rules were built ("design"); 2025 and 2026 are dates they never saw.

The bot, on 1-minute candles (an approximation of the tick-level bot in research.zone_book_study):
  - a test: price trades back to a live zone's near edge (research.zones tests), 09:30-15:30 ET
  - entry: within 5 minutes, the first 1-minute close at least 1 point off the test's extreme,
    filled at that close + 1 tick; no trade if price first gets 3 points through the edge
  - exits: 2-pt stop / 4-pt target, 3-pt stop / 7-pt target, and the owner's normal stop
    (3 points past the zone's near edge) with a target of 2x that risk (STRATEGY.md minimum)
  - one trade at a time, up to 3 a day, flat at 15:59; a candle that reaches both the stop and
    the target counts as a loss; stops pay 1 tick; $12.40 costs per trade (10 MES)
Baseline: random entries on the same days (a random minute, random side), same fixed exits.

Types of day (from that day's regular session, so they describe the day; only the gap is
known at the open):
  trend day: the close is at least half the day's range away from the open; else range day
  volatility: the day's range vs the last 20 days' average (high >= 1.2x, low <= 0.8x)
  gap: the open vs the previous close, at least 0.3x the average range

Writes research/results/journal/zone_all_days_trades.csv, zone_all_days_summary.csv and
zone_all_days_months.csv. Run from the repo root:   python -m research.zone_all_days
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from research.journal_study import OUT
from research.level_study import prepare
from research.market_data import epoch_seconds
from research.settings import load_settings
from research.zone_course_chart import es_zones
from research.zone_study import NAMES, hm, minutes_et
from research.zones import all_zones


def day_types(fb: pd.DataFrame, rth: np.ndarray) -> pd.DataFrame:
    """Regular-session open, high, low, close per session, and the type of day."""
    b = fb[rth]
    d = b.groupby("session").agg(open=("open_adj", "first"), high=("high_adj", "max"), low=("low_adj", "min"),
                                 close=("close_adj", "last"))
    rng = (d["high"] - d["low"]).clip(lower=1)
    avg = rng.rolling(20).mean().shift(1)
    d["trend_day"] = (d["close"] - d["open"]).abs() >= 0.5 * rng
    d["volatility"] = np.select([rng >= 1.2 * avg, rng <= 0.8 * avg], ["high", "low"], "normal")
    d.loc[avg.isna(), "volatility"] = "unknown"
    d["gap"] = (d["open"] - d["close"].shift(1)).abs() >= 0.3 * avg
    return d


def run_exit(lo, hi, cl, b, end, fill, d, stop_t, target_t, slip) -> tuple[float, int]:
    """Ticks per contract from the fill and the exit bar. Stop first if a bar reaches both."""
    stop, target = fill - d * stop_t, fill + d * target_t
    for k in range(b + 1, end + 1):
        if lo[k] <= stop if d == 1 else hi[k] >= stop:
            return -(stop_t + slip), k
        if hi[k] >= target if d == 1 else lo[k] <= target:
            return target_t, k
    return (cl[end] - fill) * d - slip, end


def one_market(name: str, fb: pd.DataFrame, z: pd.DataFrame, first: str, last: str, s: dict, rng) -> tuple:
    zs, bs, tick = s["zone_study"], s["book_study"], s["market"]["tick_size"]
    slip = bs["slippage_ticks"]
    turn_t, brk = round(bs["turn_points"] / tick), round(bs["test_break_points"] / tick)
    max_k = bs["max_test_minutes"]
    t = epoch_seconds(fb["ts"])
    lo, hi, cl = (fb[k].to_numpy() for k in ("low_adj", "high_adj", "close_adj"))
    mins = minutes_et(fb["ts"])
    sess = fb["session"].to_numpy()
    e0, e1, flat = hm(zs["entry_from_et"]), hm(zs["last_entry_et"]), hm(zs["flat_et"])
    rth = (mins >= e0) & (mins < hm("16:00"))
    types = day_types(fb, rth)
    t_first, t_last = pd.Timestamp(first, tz="UTC").timestamp(), pd.Timestamp(last, tz="UTC").timestamp()
    exits = [("2/4", "fixed", 2, 4), ("3/7", "fixed", 3, 7), ("3 past edge, 2x", "edge", 3, 2)]

    cands = []
    for r in z.itertuples():
        d = 1 if r.kind == "demand" else -1
        for n, tt in enumerate(r.tests, 1):
            if not (t_first <= tt < t_last):
                continue
            b = np.searchsorted(t, tt, "left")
            if not (e0 <= mins[b] < e1):
                continue
            ext, k_in = (lo[b] if d == 1 else hi[b]), None
            for k in range(b, min(b + max_k, len(t))):                   # the turn must come within 5 minutes
                if sess[k] != sess[b]:
                    break
                ext = min(ext, lo[k]) if d == 1 else max(ext, hi[k])
                if (r.near - ext) * d >= brk:
                    break                                                 # broke through first
                if (cl[k] - ext) * d >= turn_t:
                    k_in = k
                    break
            if k_in is None:
                continue
            cands.append(dict(market=name, session=sess[b], b=k_in, d=d, near=r.near, fill=cl[k_in] + d * slip,
                              tf=NAMES[r.timeframe], test=n, pattern=r.pattern))
    c = pd.DataFrame(cands).sort_values("b").reset_index(drop=True)

    trades = []
    for day, g in c.groupby("session"):
        idx = np.flatnonzero((sess == day) & (mins <= flat))
        if not len(idx):
            continue
        end = idx[-1]
        for lab, kind, st, tg in exits:
            busy, n = -1, 0
            for r in g.itertuples():
                if n >= bs["max_trades_per_day"] or r.b <= busy:
                    continue
                if kind == "fixed":
                    stop_t, target_t = round(st / tick), round(tg / tick)
                else:
                    stop_t = (r.fill - (r.near - r.d * round(st / tick))) * r.d
                    if stop_t <= 0:
                        continue
                    target_t = tg * stop_t
                pts, k_out = run_exit(lo, hi, cl, r.b, end, r.fill, r.d, stop_t, target_t, slip)
                trades.append(dict(market=name, session=day, exit=lab, tf=r.tf, test=r.test, pattern=r.pattern,
                                   points=pts * tick, risk=stop_t * tick))
                busy, n = k_out, n + 1

    rand = []
    days = c["session"].unique()
    for day in days:
        idx = np.flatnonzero((sess == day) & (mins >= e0) & (mins < e1))
        end = np.flatnonzero((sess == day) & (mins <= flat))[-1]
        for b in rng.choice(idx, zs["random_per_day"]):
            d = int(rng.choice([-1, 1]))
            for lab, kind, st, tg in exits[:2]:
                pts, _ = run_exit(lo, hi, cl, b, end, cl[b] + d * slip, d, round(st / tick), round(tg / tick), slip)
                rand.append(dict(market=name, session=day, exit=lab, points=pts * tick))
    tr, rd = pd.DataFrame(trades), pd.DataFrame(rand)
    for df in (tr, rd):
        df["trend_day"] = df["session"].map(types["trend_day"])
        df["volatility"] = df["session"].map(types["volatility"])
        df["gap"] = df["session"].map(types["gap"])
    return tr, rd, len(days)


def main() -> None:
    s = load_settings()
    rng = np.random.default_rng(s["level_study"]["random_seed"])
    cost = s["bot"]["contracts_per_trade"] * s["backtest"]["commission_per_contract_round_trip"]
    usd = s["market"]["point_value_usd"] * s["bot"]["contracts_per_trade"]

    fb_es, _, z_es, _ = es_zones(s)
    tr1, rd1, n1 = one_market("ES", fb_es, z_es, "2024-02-01", "2025-03-22", s, rng)
    fb_mes, _, _ = prepare(s)
    z_mes, _ = all_zones(fb_mes, s)
    tr2, rd2, n2 = one_market("MES", fb_mes, z_mes, "2025-04-01", "2026-10-24", s, rng)
    tr, rd = pd.concat([tr1, tr2], ignore_index=True), pd.concat([rd1, rd2], ignore_index=True)
    for df in (tr, rd):
        ses = pd.to_datetime(df["session"])
        df["year"] = ses.dt.year.astype(str)
        df["quarter"] = ses.dt.to_period("Q").astype(str)
        df["month"] = ses.dt.to_period("M").astype(str)
        df["usd"] = df["points"] * usd - cost
    tr.to_csv(OUT / "zone_all_days_trades.csv", index=False)

    rows = []
    def add(group, value, g, r):
        rows.append(dict(split=group, value=value, exit=g["exit"].iat[0] if len(g) else "", trades=len(g),
                         days=g["session"].nunique(), win_rate=(g["points"] > 0).mean() if len(g) else np.nan,
                         usd_per_trade=g["usd"].mean() if len(g) else np.nan, net_usd=g["usd"].sum(),
                         random_win_rate=(r["points"] > 0).mean() if len(r) else np.nan,
                         random_usd_per_trade=r["usd"].mean() if len(r) else np.nan))
    for ex, g in tr.groupby("exit"):
        r = rd[rd["exit"] == ex]
        add("all days", "all", g, r)
        for col in ["year", "quarter", "trend_day", "volatility", "gap", "tf"]:
            for v, gg in g.groupby(col):
                add(col, str(v), gg, r[r[col] == v] if col in r else r.iloc[0:0])
    table = pd.DataFrame(rows)
    table.to_csv(OUT / "zone_all_days_summary.csv", index=False)
    months = tr.groupby(["exit", "month"])["usd"].sum().unstack(0)
    months.to_csv(OUT / "zone_all_days_months.csv")
    pd.set_option("display.width", 250)
    pd.set_option("display.max_rows", 400)
    print(f"sessions with zone entries: ES {n1}, MES {n2}")
    print(table.round(3).to_string())
    print("months with the bot up:", {ex: f"{int((months[ex] > 0).sum())} of {int(months[ex].notna().sum())}" for ex in months})


if __name__ == "__main__":
    main()
