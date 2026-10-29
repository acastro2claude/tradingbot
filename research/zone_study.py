"""How price behaves at the owner-style supply/demand zones (research.zones), ES Feb 2024 - Mar 2025.

Two questions, using only the ES 1-minute bars and the owner's journal (no new data):

1. The owner's own trades: were they taken at the bot's zones? A trade is "at a zone" when
   it goes the zone's way (long at demand, short at supply) and the entry is inside a live
   zone, up to near_points outside its near edge or far_points past its far edge. Reported
   by zone timeframe and by which test of the zone it was. Results use the journal's own
   exits and dollars.

2. Every test of every zone, traded blindly: a limit order at the near edge, filled when
   price trades 1 tick through it, during regular hours (09:30-15:30 ET entries, flat at
   15:59). Stop and target from [zone_study] exits (points from the fill; the owner's normal
   stop is 3 points past the level). On 1-minute bars a bar that reaches both the stop and
   the target counts as a loss. Stops pay 1 tick of slippage; $12.40 costs per trade (10 MES).
   Baseline: random entries on the same days (market order + 1 tick), same exits, and the
   break-even win rate of a coin flip (stop / (stop + target)).
   Ranges are 95% bootstrap ranges resampling whole days.

3. Does the zone hold as an area? The same tests with the stop 1 point past the far edge and
   targets 1x and 2x that risk, against random entries with the same distances (one per test).

Writes research/results/journal/zone_study_owner.csv, zone_study_owner_summary.csv,
zone_study_tests.csv, zone_study_summary.csv and zone_study_hold.csv. Run from the repo root:   python -m research.zone_study
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from research.journal_study import OUT
from research.level_test_study import owner_trades
from research.market_data import epoch_seconds
from research.settings import load_settings
from research.zone_course_chart import es_zones

NAMES = {15: "15m", 30: "30m", 60: "1h", 120: "2h", 240: "4h"}


def minutes_et(ts: pd.Series) -> np.ndarray:
    loc = ts.dt.tz_convert("America/New_York")
    return (loc.dt.hour * 60 + loc.dt.minute).to_numpy()


def hm(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


# ------------------------------------------------------------------ 1. owner's trades at zones

def owner_at_zones(z: pd.DataFrame, fb: pd.DataFrame, zs: dict, tick: float) -> pd.DataFrame:
    t_bar = epoch_seconds(fb["ts"])
    tr = owner_trades()
    near, far = zs["near_points"] / tick, zs["far_points"] / tick
    rows = []
    for r in tr.itertuples():
        i = np.searchsorted(t_bar, r.t, "right") - 1
        contract = r.contract[:-2] + r.contract[-1]                          # journal ESH24 -> data ESH4
        if i < 0 or r.t - t_bar[i] > 120 or fb["contract"].iat[i] != contract:
            rows.append(dict(trade_id=r.trade_id, at="no bar / other contract"))
            continue
        adj = r.price + int(fb["offset_ticks"].iat[i])                       # entry in adjusted ticks
        live = z[(z["created"] <= r.t) & (z["dies"] > r.t)]
        d = int(r.direction)
        dem = live["kind"].eq("demand").to_numpy()
        lo, hi = live["low"].to_numpy(), live["high"].to_numpy()
        with_long = dem & (adj >= lo - far) & (adj <= hi + near)            # long at demand
        with_short = ~dem & (adj <= hi + far) & (adj >= lo - near)           # short at supply
        ours = with_long if d == 1 else with_short
        against = with_short if d == 1 else with_long                        # long into supply, short into demand
        if ours.any():
            m = live[ours].sort_values("timeframe").iloc[-1]                  # the highest timeframe
            n_test = sum(1 for x in m.tests if x <= r.t + 60)
            rows.append(dict(trade_id=r.trade_id, at="at a zone, its way", tf=NAMES[m.timeframe],
                             test=max(n_test, 1), pattern=m.pattern))
        elif against.any():
            rows.append(dict(trade_id=r.trade_id, at="into a zone, against it"))
        else:
            rows.append(dict(trade_id=r.trade_id, at="away from zones"))
    out = tr.merge(pd.DataFrame(rows), on="trade_id")
    return out


def owner_table(t: pd.DataFrame) -> pd.DataFrame:
    def row(name, g):
        return dict(group=name, trades=len(g), win_rate=(g["points"] > 0).mean() if len(g) else np.nan,
                    avg_points=g["points"].mean() if len(g) else np.nan, net_usd=g["net_usd"].sum(),
                    usd_per_trade=g["net_usd"].mean() if len(g) else np.nan)
    rows = [row("all trades", t)]
    for a in ["at a zone, its way", "into a zone, against it", "away from zones"]:
        rows.append(row(a, t[t["at"] == a]))
    z = t[t["at"] == "at a zone, its way"].reindex(columns=list(t.columns) + ["tf", "test"])
    z = z.loc[:, ~z.columns.duplicated()]
    for tf in ["4h", "2h", "1h", "30m", "15m"]:
        rows.append(row(f"  at a {tf} zone", z[z["tf"] == tf]))
    rows.append(row("  tests 1-3", z[z["test"] <= 3]))
    rows.append(row("  test 4 or later", z[z["test"] >= 4]))
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ 2. every zone test, traded blindly

def run_exit(lo, hi, cl, b, end, fill, d, stop_t, target_t, slip):
    """Points per contract from the fill: stop first if a bar reaches both. Flat at `end` (bar index)."""
    stop, target = fill - d * stop_t, fill + d * target_t
    for k in range(b, end + 1):
        hit_stop = lo[k] <= stop if d == 1 else hi[k] >= stop
        if hit_stop:
            return -(stop_t + slip), "stop"
        if k > b and (hi[k] >= target if d == 1 else lo[k] <= target):
            return target_t, "target"
    return (cl[end] - fill) * d - slip, "flat"


def zone_tests(z: pd.DataFrame, fb: pd.DataFrame, zs: dict, s: dict) -> pd.DataFrame:
    tick = s["market"]["tick_size"]
    t = epoch_seconds(fb["ts"])
    lo, hi, cl = (fb[k].to_numpy() for k in ("low_adj", "high_adj", "close_adj"))
    mins = minutes_et(fb["ts"])
    sess = fb["session"].to_numpy()
    start = pd.Timestamp(zs["period_start"], tz="UTC").timestamp()
    e0, e1, flat = hm(zs["entry_from_et"]), hm(zs["last_entry_et"]), hm(zs["flat_et"])
    slip = s["book_study"]["slippage_ticks"]
    rows = []
    for r in z.itertuples():
        d = 1 if r.kind == "demand" else -1
        for n, tt in enumerate(r.tests, 1):
            if tt < start:
                continue
            b = np.searchsorted(t, tt, "left")
            if not (e0 <= mins[b] < e1):
                continue
            through = lo[b] <= r.near - 1 if d == 1 else hi[b] >= r.near + 1   # limit filled: traded 1 tick through
            if not through:
                continue
            day = sess[b]
            end = b + np.searchsorted(sess[b:], day, "right") - 1               # last bar of the session...
            end = min(end, b + np.searchsorted(mins[b:end + 1] >= flat, True))  # ...or the 15:59 bar
            row = dict(day=pd.Timestamp(day).date().isoformat(), time=tt, tf=NAMES[r.timeframe], kind=r.kind,
                       pattern=r.pattern, test=n, height=(r.high - r.low) * tick)
            for st, tg in zs["exits"]:
                pts, how = run_exit(lo, hi, cl, b, end, r.near, d, round(st / tick), round(tg / tick), slip)
                row[f"pts {st:g}/{tg:g}"] = pts * tick
                row[f"how {st:g}/{tg:g}"] = how
            # does the zone hold as an area? stop 1 point past the far edge, target 1x and 2x that risk
            risk = abs(r.near - r.far) + round(zs["hold_stop_past_far_points"] / tick)
            for m in zs["hold_targets_x_risk"]:
                pts, _ = run_exit(lo, hi, cl, b, end, r.near, d, risk, round(m * risk), slip)
                row[f"hold {m:g}x"] = pts * tick
            row["risk_points"] = risk * tick
            rows.append(row)
    return pd.DataFrame(rows)


def random_matched(fb: pd.DataFrame, tests: pd.DataFrame, zs: dict, s: dict, rng) -> pd.DataFrame:
    """For each zone test, one random entry (random RTH minute on a random test day, random side)
    with the same stop and target distances as the zone-holds exits."""
    tick = s["market"]["tick_size"]
    lo, hi, cl, op = (fb[k].to_numpy() for k in ("low_adj", "high_adj", "close_adj", "open_adj"))
    mins = minutes_et(fb["ts"])
    sess = fb["session"].dt.date.astype(str).to_numpy()
    e0, e1, flat = hm(zs["entry_from_et"]), hm(zs["last_entry_et"]), hm(zs["flat_et"])
    slip = s["book_study"]["slippage_ticks"]
    days = tests["day"].unique()
    rth = {d: np.flatnonzero((sess == d) & (mins >= e0) & (mins < e1)) for d in days}
    last = {d: np.flatnonzero((sess == d) & (mins <= flat))[-1] for d in days}
    rows = []
    for r in tests.itertuples():
        day = rng.choice(days)
        if not len(rth[day]):
            continue
        b, d = rng.choice(rth[day]), int(rng.choice([-1, 1]))
        risk = round(r.risk_points / tick)
        row = dict(day=day, risk_points=r.risk_points)
        for m in zs["hold_targets_x_risk"]:
            pts, _ = run_exit(lo, hi, cl, b, last[day], op[b] + d * slip, d, risk, round(m * risk), slip)
            row[f"hold {m:g}x"] = pts * tick
        rows.append(row)
    return pd.DataFrame(rows)


def random_entries(fb: pd.DataFrame, days: list, zs: dict, s: dict, rng) -> pd.DataFrame:
    tick = s["market"]["tick_size"]
    lo, hi, cl, op = (fb[k].to_numpy() for k in ("low_adj", "high_adj", "close_adj", "open_adj"))
    mins = minutes_et(fb["ts"])
    sess = fb["session"].dt.date.astype(str).to_numpy()
    e0, e1, flat = hm(zs["entry_from_et"]), hm(zs["last_entry_et"]), hm(zs["flat_et"])
    slip = s["book_study"]["slippage_ticks"]
    rows = []
    for day in days:
        idx = np.flatnonzero((sess == day) & (mins >= e0) & (mins < e1))
        if not len(idx):
            continue
        last = np.flatnonzero((sess == day) & (mins <= flat))[-1]
        for b in rng.choice(idx, zs["random_per_day"]):
            d = int(rng.choice([-1, 1]))
            fill = op[b] + d * slip
            row = dict(day=day)
            for st, tg in zs["exits"]:
                pts, _ = run_exit(lo, hi, cl, b, last, fill, d, round(st / tick), round(tg / tick), slip)
                row[f"pts {st:g}/{tg:g}"] = pts * tick
            rows.append(row)
    return pd.DataFrame(rows)


def summary(tests: pd.DataFrame, rand: pd.DataFrame, zs: dict, s: dict, rng) -> pd.DataFrame:
    cost = s["bot"]["contracts_per_trade"] * s["backtest"]["commission_per_contract_round_trip"]
    usd = s["market"]["point_value_usd"] * s["bot"]["contracts_per_trade"]
    groups = {"every zone test": np.ones(len(tests), bool)}
    for tf in ["4h", "2h", "1h", "30m", "15m"]:
        groups[f"{tf} zones"] = tests["tf"] == tf
    groups["first test"] = tests["test"] == 1
    groups["tests 2-3"] = tests["test"].between(2, 3)
    groups["test 4 or later"] = tests["test"] >= 4
    groups["continuation (RBR, DBD)"] = tests["pattern"].isin(["RBR", "DBD"])
    groups["reversal (DBR, RBD)"] = tests["pattern"].isin(["DBR", "RBD"])
    groups["demand"] = tests["kind"] == "demand"
    groups["supply"] = tests["kind"] == "supply"
    rows = []
    for st, tg in zs["exits"]:
        col = f"pts {st:g}/{tg:g}"
        coin = st / (st + tg)
        r_win = (rand[col] > 0).mean()
        r_daily = rand.groupby("day")[col].agg(lambda v: (v > 0).mean())
        for name, m in groups.items():
            g = tests[np.asarray(m, bool)]
            if not len(g):
                continue
            win = (g[col] > 0).mean()
            # 95% range of (win rate - random win rate), resampling whole days
            days = g["day"].unique()
            by_day = g.groupby("day")[col].agg(["size", lambda v: (v > 0).sum()])
            by_day.columns = ["n", "w"]
            diffs = []
            for _ in range(s["level_study"]["bootstrap_samples"] // 4):
                pick = rng.choice(days, len(days))
                n, w = by_day.loc[pick, "n"].sum(), by_day.loc[pick, "w"].sum()
                diffs.append(w / n - r_daily.reindex(pick).mean())
            lo95, hi95 = np.nanpercentile(diffs, [2.5, 97.5])
            rows.append(dict(exit=f"stop {st:g} / target {tg:g}", group=name, trades=len(g), days=len(days),
                             win_rate=win, random_win_rate=r_win, coin_flip=coin,
                             vs_random_lo=lo95, vs_random_hi=hi95,
                             usd_per_trade=g[col].mean() * usd - cost, random_usd_per_trade=rand[col].mean() * usd - cost))
    return pd.DataFrame(rows)


def main() -> None:
    s = load_settings()
    zs = s["zone_study"]
    rng = np.random.default_rng(s["level_study"]["random_seed"])
    fb, sessions, z, _ = es_zones(s)

    own = owner_at_zones(z, fb, zs, s["market"]["tick_size"])
    own.to_csv(OUT / "zone_study_owner.csv", index=False)
    tables = []
    for period, start in [("all journal", "2024-01-01"), ("disciplined period", zs["reference_start"])]:
        tb = owner_table(own[own["day"] >= start])
        tb.insert(0, "period", period)
        tables.append(tb)
    owner = pd.concat(tables, ignore_index=True)
    owner.to_csv(OUT / "zone_study_owner_summary.csv", index=False)

    tests = zone_tests(z, fb, zs, s)
    tests.to_csv(OUT / "zone_study_tests.csv", index=False)
    rand = random_entries(fb, sorted(tests["day"].unique()), zs, s, rng)
    table = summary(tests, rand, zs, s, rng)
    table.to_csv(OUT / "zone_study_summary.csv", index=False)

    # zone holds as an area (stop past the far edge) vs matched random entries
    match = random_matched(fb, tests, zs, s, rng)
    hold = []
    for m in zs["hold_targets_x_risk"]:
        col = f"hold {m:g}x"
        for name, g in [("every zone test", tests)] + [(f"{tf} zones", tests[tests["tf"] == tf]) for tf in NAMES.values()] \
                + [("first test", tests[tests["test"] == 1]), ("test 4 or later", tests[tests["test"] >= 4])]:
            gm = match.loc[g.index.intersection(match.index)]
            hold.append(dict(target=f"{m:g}x the risk", group=name, trades=len(g), median_risk_points=g["risk_points"].median(),
                             win_rate=(g[col] > 0).mean(), matched_random_win_rate=(gm[col] > 0).mean(),
                             coin_flip=1 / (1 + m)))
    hold = pd.DataFrame(hold)
    hold.to_csv(OUT / "zone_study_hold.csv", index=False)
    print(hold.round(3).to_string())

    pd.set_option("display.width", 250)
    print(owner.round(3).to_string())
    print(table.round(3).to_string())


if __name__ == "__main__":
    main()
