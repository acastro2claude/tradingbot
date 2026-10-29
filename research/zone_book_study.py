"""The order book at the owner-style zones, and bot vs owner, on the 23 ES days with the full book.

Same pieces as research.bot_week_study (five 2024 weeks, Databento MBO), but the marked
levels are now the owner-style supply/demand zones (research.zones, owner-approved Oct 2026):
  1. rebuild each day's ES book from 08:00 to 16:00 ET
  2. find every test of a live zone's near edge between 09:30 and 15:30 ET (price comes
     back to the edge after being 5 points away, research.level_test_book.find_tests)
  3. read the book at each test (line before, eaten, stacking, delta, absorption) and the
     owner's reasons from the blind review (research.owner_rules: other side in control,
     weaker retest, absorption spike, delta divergence, zone in the way)
  4. trade each test that turns (1 point off its extreme, market fill + 1 tick) with every
     exit style in [[book_study.exits]]. For the "past the level" stop the level is the
     zone's near edge: the owner keeps the normal stop even when the zone is big.
  5. compare reads, zone timeframes and test numbers; random entries on the same days with
     the same fixed exits; and the bot (rules A-E, one trade at a time, up to 3 a day)
     against the owner's own trades, week by week.

Money: 10 MES = 1 ES ($50 a point), costs 10 x $1.24; the owner's actual net $ is shown.
Writes research/results/journal/zone_book_tests.csv, zone_book_reads.csv,
zone_book_bot_vs_owner.csv. Run from the repo root:
    python -m research.zone_book_study            (add --reuse to skip rebuilding the books)
"""

from __future__ import annotations

import sys
from multiprocessing import Pool
from types import SimpleNamespace

import numpy as np
import pandas as pd

from research.bot_week_study import (BOOK_DIR, BURST_MIN, CONTRACTS, ES_POINT, FLAT_ET, RULES, SLIP_TICKS, bot_trades,
                                     et, matched_owner, trade_exit)
from research.exit_study import day_context, one_exit
from research.journal_study import OUT
from research.level_test_book import TestReader, day_bursts, find_tests, marked_levels, owner_traded
from research.level_test_study import owner_trades
from research.market_data import session_dates
from research.orderbook import build_book, check_book, load_mbo
from research.orderbook_study import typical_depth
from research.owner_rules import R, DayTrades, features
from research.settings import load_settings
from research.zone_course_chart import es_zones

NAMES = {15: "15m", 30: "30m", 60: "1h", 120: "2h", 240: "4h"}
CTX = {}


def zone_levels(zones: pd.DataFrame, off: int, t0: float, t1: float) -> pd.DataFrame:
    """Live zones in the book's contract: edge = near edge (top of demand, bottom of supply)."""
    live = zones[(zones["created"] <= t1) & (zones["dies"] > t0)]
    return pd.DataFrame(dict(source="zone", direction=np.where(live["kind"] == "demand", 1, -1),
                             edge=(live["near"] - off).astype(int).to_numpy(), lo=(live["low"] - off).astype(int).to_numpy(),
                             hi=(live["high"] - off).astype(int).to_numpy(), created=live["created"].to_numpy(),
                             dies=live["dies"].to_numpy()))


def zone_of(test, zones: pd.DataFrame, off: int) -> dict:
    """The zone a test belongs to (highest timeframe if several): timeframe, pattern, which test, POC inside."""
    m = zones[(zones["created"] <= test.time) & (zones["dies"] > test.time)
              & ((zones["kind"] == "demand") == (test.direction == 1)) & (zones["near"] - off == test.edge)]
    if m.empty:
        return dict(tf=None, pattern=None, zone_test=np.nan, height=np.nan)
    z = m.sort_values("timeframe").iloc[-1]
    n = sum(1 for x in z.tests if x <= test.time + 60)
    return dict(tf=NAMES[z.timeframe], pattern=z.pattern, zone_test=max(n, 1), height=(z.high - z.low) * 0.25)


def one_day(row) -> pd.DataFrame | None:
    s, sessions, zones, offsets = CTX["s"], CTX["sessions"], CTX["zones"], CTX["offsets"]
    f = BOOK_DIR / f"{row['es']}_{row['day']}_mbo.dbn.zst"
    if not f.exists() or row["es"] not in offsets.index:
        return None
    day, off = row["day"], int(offsets[row["es"]])
    t0, t1 = et(day, "09:30"), et(day, "15:30")
    book = build_book(load_mbo(f), int(et(day, "08:00")), int(et(day, "16:00")))
    reader = TestReader(book, typical_depth(book), day_bursts(book, s["bot"]["absorption_burst_seconds"], 20))
    levels = zone_levels(zones, off, t0, t1)
    tests = find_tests(book, levels, t0, t1)
    if tests.empty:
        print(f"{day}: no zone tests", flush=True)
        return None
    reads = pd.DataFrame([reader.read(x.time, x.direction, x.lo, x.hi, x.edge) for x in tests.itertuples()])
    tests = pd.concat([tests.reset_index(drop=True), reads], axis=1)
    meta = pd.DataFrame([zone_of(x, zones, off) for x in tests.itertuples()])
    tests = pd.concat([tests, meta], axis=1)
    session = pd.Timestamp(session_dates(pd.Series([pd.Timestamp(et(day, "12:00"), unit="s", tz="UTC")])).iat[0])
    pocs = marked_levels(sessions, zones.iloc[0:0], session, off, t0, t1, s["bot"]["recent_poc_lookback_sessions"])
    poc_px = pocs["edge"].to_numpy() if len(pocs) else np.array([])
    tests["poc_inside"] = [bool(((poc_px >= x.lo) & (poc_px <= x.hi)).any()) for x in tests.itertuples()]
    flat = et(day, FLAT_ET)
    ex = [trade_exit(reader, r.entry_time, r.entry_price, r.direction, flat) if r.turn == "turned" else (np.nan,) * 3
          for r in tests.itertuples()]
    tests[["points", "exit_time", "exit_how"]] = pd.DataFrame(ex, index=tests.index)
    tests["day"], tests["es"], tests["week"], tests["typical_size"] = day, row["es"], row["week"], reader.typ
    chk = check_book(book)
    print(f"{day} {row['es']}: crossed {chk['crossed_seconds']} s, {len(levels)} zones live, {len(tests)} tests, "
          f"{int((tests['turn'] == 'turned').sum())} entries", flush=True)
    return tests


def add_exits_and_reasons(tests: pd.DataFrame, s: dict) -> pd.DataFrame:
    """Every exit style, and the owner's reasons (owner_rules.features), for each test that turned."""
    exits = s["book_study"]["exits"]
    rows = []
    for day, g in tests.groupby("day"):
        es = g["es"].iat[0]
        ctx, dt, flat = day_context(es, day, s), DayTrades(es, day), et(day, FLAT_ET)
        zones_now = g[["direction", "lo", "hi"]].drop_duplicates()
        for r in g.itertuples():
            out = dict(idx=r.Index)
            if r.turn == "turned":
                at_edge = SimpleNamespace(**{**r._asdict(), "lo": r.edge, "hi": r.edge})   # the level = the near edge
                for ex in exits:
                    res = one_exit(ctx, at_edge, ex, s, flat)
                    out[f"taken | {ex['name']}"] = res.get("taken", False)
                    out[f"points | {ex['name']}"] = res.get("points", np.nan)
                    out[f"exit_time | {ex['name']}"] = res.get("exit_time", np.nan)
                earlier = g[(g["edge"] == r.edge) & (g["direction"] == r.direction) & (g["time"] < r.time)
                            & (g["time"] >= r.time - R["retest_within_min"] * 60)]
                prev = float(earlier["time"].max()) if len(earlier) else None
                out.update(features(dt, r, prev, zones_now))
            rows.append(out)
    extra = pd.DataFrame(rows).set_index("idx")
    return tests.join(extra)


def random_entries(tests: pd.DataFrame, s: dict, rng, per_day: int) -> pd.DataFrame:
    """Random entries on the same days (09:30-15:30, random side), with the fixed-stop exits."""
    fixed = [ex for ex in s["book_study"]["exits"] if ex["stop"] == "fixed" and ex["target"] == "fixed"]
    rows = []
    for day, g in tests.groupby("day"):
        es = g["es"].iat[0]
        ctx, flat = day_context(es, day, s), et(day, FLAT_ET)
        for t in np.sort(rng.uniform(et(day, "09:30"), et(day, "15:30"), per_day)):
            i = int(np.searchsorted(ctx.t, t, "right"))
            if i >= len(ctx.p):
                continue
            d = int(rng.choice([-1, 1]))
            r = SimpleNamespace(direction=d, entry_price=int(ctx.p[i]), entry_time=float(ctx.t[i]),
                                test_extreme=int(ctx.p[i]), lo=int(ctx.p[i]), hi=int(ctx.p[i]))
            row = dict(day=day)
            for ex in fixed:
                row[f"points | {ex['name']}"] = one_exit(ctx, r, ex, s, flat).get("points", np.nan)
            rows.append(row)
    return pd.DataFrame(rows)


def read_groups(e: pd.DataFrame) -> dict:
    full = (e["line_before"] >= 2) & (e["line_left_share"] >= 0.5) & (e["eaten"] > 0) & (e["stacking"] > 0) \
        & (e["delta_10s"] > 0) & (e["absorption_max"] >= BURST_MIN)
    g = {"every zone test that turned": np.ones(len(e), bool)}
    for tf in NAMES.values():
        g[f"  {tf} zones"] = e["tf"] == tf
    g.update({
        "first test of the zone": e["zone_test"] == 1,
        "tests 2-3": e["zone_test"].between(2, 3),
        "test 4 or later": e["zone_test"] >= 4,
        "recent uncovered POC inside the zone": e["poc_inside"],
        "line 2x+ at the edge before price got there": e["line_before"] >= 2,
        "line still there when price arrived": (e["line_before"] >= 2) & (e["line_left_share"] >= 0.5),
        "orders eaten at the edge": e["eaten"] > 0,
        "more orders joined than were pulled (stacking)": e["stacking"] > 0,
        "eaten + stacking": (e["eaten"] > 0) & (e["stacking"] > 0),
        "delta last 10 s the trade's way": e["delta_10s"] > 0,
        f"absorption {BURST_MIN}+ at the edge": e["absorption_max"] >= BURST_MIN,
        "full read (line held, eaten, stacking, delta, absorption)": full,
        "other side still in control (owner: wait)": e["control"].astype(bool),
        "other side still too aggressive": e["aggression"].astype(bool),
        "neither (control or aggression)": ~e["control"].astype(bool) & ~e["aggression"].astype(bool),
        "weaker retest": e["weaker_retest"].astype(bool),
        "retest, not weaker": e["retest"].astype(bool) & ~e["weaker_retest"].astype(bool),
        "absorption spike": e["spike"].astype(bool),
        "delta divergence": e["divergence"].astype(bool),
        "opposite zone within 4 points": e["zone_conflict"].astype(bool),
        "you traded this test": e["owner_points"].notna(),
        "you did not trade this test": e["owner_points"].isna(),
    })
    return g


def reads_table(tests: pd.DataFrame, rand: pd.DataFrame, s: dict) -> pd.DataFrame:
    cost = CONTRACTS * s["backtest"]["commission_per_contract_round_trip"]
    e = tests[tests["turn"] == "turned"]
    rows = []
    for ex in s["book_study"]["exits"]:
        n = ex["name"]
        taken = e[f"taken | {n}"].fillna(False).astype(bool)
        for name, m in read_groups(e).items():
            g = e[np.asarray(m, bool) & taken.to_numpy()]
            rows.append(dict(exit=n, read=name, trades=len(g), win_rate=(g[f"points | {n}"] > 0).mean() if len(g) else np.nan,
                             usd_per_trade=g[f"points | {n}"].mean() * ES_POINT - cost if len(g) else np.nan))
        col = f"points | {n}"
        if col in rand:
            rows.append(dict(exit=n, read="random entries, same days", trades=len(rand),
                             win_rate=(rand[col] > 0).mean(), usd_per_trade=rand[col].mean() * ES_POINT - cost))
    return pd.DataFrame(rows)


def bot_vs_owner(tests: pd.DataFrame, owner: pd.DataFrame, s: dict) -> pd.DataFrame:
    cost = CONTRACTS * s["backtest"]["commission_per_contract_round_trip"]
    rows = []
    for week, ow in owner.groupby("week"):
        rows.append(dict(week=week, exit="(your own exits)", who="you (journal)", trades=len(ow),
                         win_rate=(ow["points"] > 0).mean(), net_usd=ow["net_usd"].sum(), same_as_you=np.nan))
    for ex in s["book_study"]["exits"]:
        n = ex["name"]
        te = tests.copy()
        te["points"], te["exit_time"] = te[f"points | {n}"], te[f"exit_time | {n}"]
        te.loc[~te[f"taken | {n}"].fillna(False).astype(bool), "turn"] = "skipped by the exit rule"
        for week, wt in te.groupby("week"):
            for name, rule in RULES.items():
                b = bot_trades(wt, rule, False)
                rows.append(dict(week=week, exit=n, who=f"bot {name}", trades=len(b),
                                 win_rate=(b["points"] > 0).mean() if len(b) else np.nan,
                                 net_usd=b["points"].sum() * ES_POINT - cost * len(b),
                                 same_as_you=matched_owner(b, owner[owner["week"] == week])))
    return pd.DataFrame(rows)


def main() -> None:
    s = load_settings()
    rng = np.random.default_rng(s["level_study"]["random_seed"])
    plan = pd.read_csv(OUT / "es_week_price_plan.csv")
    have = [r for _, r in plan.iterrows() if (BOOK_DIR / f"{r['es']}_{r['day']}_mbo.dbn.zst").exists()]
    days = [r["day"] for r in have]
    owner = owner_trades()
    owner = owner[owner["day"].isin(days)].copy()
    owner["week"] = owner["day"].map(plan.set_index("day")["week"])
    saved = OUT / "zone_book_tests.csv"
    if "--reuse" in sys.argv and saved.exists():
        tests = pd.read_csv(saved)
    else:
        _, sessions, zones, _ = es_zones(s)
        offsets = sessions.groupby("contract")["offset_ticks"].first()
        CTX.update(s=s, sessions=sessions, zones=zones, offsets=offsets)
        with Pool(2) as pool:                    # big ES days need several GB each
            parts = pool.map(one_day, have)
        tests = pd.concat([p for p in parts if p is not None], ignore_index=True)
        tests["owner_points"] = np.nan
        for day, g in tests.groupby("day"):
            tests.loc[g.index, "owner_points"] = owner_traded(g, owner[owner["day"] == day]).to_numpy()
        tests = add_exits_and_reasons(tests, s)
        tests.to_csv(saved, index=False)
    rand = random_entries(tests, s, rng, s["zone_study"]["random_per_day"])
    reads = reads_table(tests, rand, s)
    reads.to_csv(OUT / "zone_book_reads.csv", index=False)
    res = bot_vs_owner(tests, owner, s)
    res.to_csv(OUT / "zone_book_bot_vs_owner.csv", index=False)
    total = res.groupby(["exit", "who"], sort=False).agg(trades=("trades", "sum"), net_usd=("net_usd", "sum"),
                                                         same_as_you=("same_as_you", "sum")).reset_index()
    ref = res[res["week"] >= s["journal_rules"]["reference_start"]]
    total_ref = ref.groupby(["exit", "who"], sort=False).agg(trades=("trades", "sum"), net_usd=("net_usd", "sum")).reset_index()
    total.to_csv(OUT / "zone_book_bot_vs_owner_total.csv", index=False)
    pd.set_option("display.width", 250)
    print(reads.round(3).to_string())
    print(total.round(1).to_string())
    print("\ndisciplined period weeks only")
    print(total_ref.round(1).to_string())


if __name__ == "__main__":
    main()
