"""Run the owner's level-test read (research.level_test_book) over the journal days with a book.

Books: data/raw/journal_mbo (MES, 20 busiest days) or data/raw/journal_es_mbo (ES,
when bought). Levels from the ES 1-minute bars. Writes level_tests_<market>.csv and
level_tests_<market>_summary.csv in research/results/journal/.

Run from the repo root:   python -m research.level_test_study MES   (or ES)
"""

from __future__ import annotations

import sys

import numpy as np
import pandas as pd

from research.journal_replay import es_context
from research.journal_study import OUT
from research.level_study import RAW
from research.level_test_book import TestReader, day_bursts, find_tests, marked_levels, owner_traded
from research.market_data import session_dates
from research.orderbook import build_book, check_book, load_mbo
from research.orderbook_study import LOOKBACK_S, typical_depth
from research.settings import load_settings

BOOKS = {"MES": (RAW / "journal_mbo", "orderbook_price_plan.csv", "mes"),
         "ES": (RAW / "journal_es_mbo", "es_orderbook_price_plan.csv", "es")}
BURST_SIZES = {"MES": 100, "ES": 50}     # proposed for ES: the owner's MES setting is 100; ES setting TBD


def owner_trades() -> pd.DataFrame:
    t = pd.read_csv(OUT / "journal_trades.csv")
    for c in ("entry_time", "exit_time"):
        t[c] = pd.to_datetime(t[c], utc=True)
    t = t[t["closed"]].copy()
    t["day"] = t["entry_time"].dt.tz_convert("America/New_York").dt.date.astype(str)
    t["t"] = t["entry_time"].map(pd.Timestamp.timestamp)
    t["direction"] = np.where(t["side"] == "long", 1, -1)
    t["price"] = np.rint(t["entry"] / 0.25).astype(np.int64)
    return t


def summarise(x: pd.DataFrame, burst_min: int) -> pd.DataFrame:
    e = x[x["turn"] == "turned"]
    hit = e["outcome_2_4"] == "target"
    conds = {
        "every test that turned (entry)": np.ones(len(e), bool),
        "POC and zone lined up": e["sources"].str.contains(r"\+"),
        "line 2x+ at the level before price got there": e["line_before"] >= 2,
        "line still there when price arrived (half or more left)": (e["line_before"] >= 2) & (e["line_left_share"] >= 0.5),
        "orders eaten at the level during the test": e["eaten"] > 0,
        "more orders joined than were pulled (stacking)": e["stacking"] > 0,
        "delta last 30 s the trade's way": e["delta_30s"] > 0,
        f"fitting absorption {burst_min}+ at the level": e["absorption_max"] >= burst_min,
        "line before + stacking + delta": (e["line_before"] >= 2) & (e["stacking"] > 0) & (e["delta_30s"] > 0),
        "full read: line before + stacking + delta + absorption": (e["line_before"] >= 2) & (e["stacking"] > 0)
        & (e["delta_30s"] > 0) & (e["absorption_max"] >= burst_min),
        "you traded this test": e["owner_points"].notna(),
        "you did not trade this test": e["owner_points"].isna(),
    }
    rows = []
    for name, m in conds.items():
        m = np.asarray(m, bool)
        rows.append(dict(read=name, tests=int(m.sum()), target_first=hit[m].mean() if m.any() else np.nan,
                         without=hit[~m].mean() if (~m).any() else np.nan,
                         you_traded=e["owner_points"].notna()[m].mean() if m.any() else np.nan))
    return pd.DataFrame(rows)


def main() -> None:
    market = sys.argv[1] if len(sys.argv) > 1 else "MES"
    book_dir, plan_file, sym_col = BOOKS[market]
    s = load_settings()
    sessions, zones, offsets = es_context(s)
    plan = pd.read_csv(OUT / plan_file)
    owner = owner_trades()
    lookback = s["bot"]["recent_poc_lookback_sessions"]
    parts = []
    for r in plan.itertuples():
        sym = getattr(r, sym_col)
        f = book_dir / f"{sym}_{r.day}_mbo.dbn.zst"
        if not f.exists():
            continue
        es = "ES" + sym[-2:]
        if es not in offsets.index:
            continue
        start = pd.Timestamp(r.start, tz="UTC").timestamp()
        end = pd.Timestamp(r.end, tz="UTC").timestamp()
        book = build_book(load_mbo(f), int(start) - LOOKBACK_S, int(end))
        reader = TestReader(book, typical_depth(book), day_bursts(book, s["bot"]["absorption_burst_seconds"], 20))
        session = session_dates(pd.Series([pd.Timestamp(start, unit="s", tz="UTC")])).iat[0]
        levels = marked_levels(sessions, zones, pd.Timestamp(session), int(offsets[es]), start, end, lookback)
        tests = find_tests(book, levels, start, end)
        if tests.empty:
            print(f"{r.day}: no tests of marked levels in the window")
            continue
        reads = pd.DataFrame([reader.read(x.time, x.direction, x.lo, x.hi, x.edge) for x in tests.itertuples()])
        tests = pd.concat([tests.reset_index(drop=True), reads], axis=1)
        mine = owner[owner["day"] == r.day]
        tests["owner_points"] = owner_traded(tests, mine).to_numpy()
        tests["day"] = r.day
        tests["typical_size"] = reader.typ
        n_mine = len(mine)
        print(f"{r.day} {sym}: {check_book(book)['crossed_seconds']} crossed s, {len(levels)} levels, "
              f"{len(tests)} tests, {int((tests['turn'] == 'turned').sum())} turned, "
              f"you traded {int(tests['owner_points'].notna().sum())} of them; your trades that day {n_mine}", flush=True)
        parts.append(tests)
    x = pd.concat(parts, ignore_index=True)
    x.to_csv(OUT / f"level_tests_{market.lower()}.csv", index=False)
    burst_min = BURST_SIZES[market]
    table = summarise(x, burst_min)
    table.to_csv(OUT / f"level_tests_{market.lower()}_summary.csv", index=False)

    days = x["day"].unique()
    yours = owner[owner["day"].isin(days)]
    at_level = x["owner_points"].notna().sum()
    print(f"\n{len(x)} tests of marked levels, {int((x['turn'] == 'turned').sum())} turned "
          f"({(x['turn'] == 'broke first').mean():.0%} broke 3 pts through first)")
    print(f"your trades on these days: {len(yours)}; at a marked-level test: {at_level}")
    pd.set_option("display.width", 250)
    print(table.round(3).to_string())


if __name__ == "__main__":
    main()
