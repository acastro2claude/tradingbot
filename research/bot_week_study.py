"""Bot vs owner, week by week, on the ES full order book (five weeks of 2024).

For every regular session in research/results/journal/es_week_price_plan.csv:
  1. rebuild the ES book (Databento MBO) from 08:00 to 16:00 ET
  2. find every test of a marked level (recent uncovered POCs and 15-minute
     zones from the ES 1-minute bars) between 09:30 and 15:30 ET
  3. read the owner's signals at each test (research.level_test_book)
  4. let the bot trade the tests that pass a rule, one position at a time, up to
     3 trades a day: market entry at the bid/ask once price turns 1 point off the
     test's extreme, plus 1 tick slippage; stop 2 points, target 4 points (the
     owner's median loss and win); flat at 15:59 ET
  5. compare with the owner's own trades that week

Rules compared (STRATEGY.md section 2, "The read in the owner's words"):
  A every test of a marked level
  B the line held: a line (2x+ the day's typical size per price) at the level
    before price got there, with half or more still there when it arrived
  C B, and the orders got eaten and more joined than were pulled
  D the full read: C, delta the trade's way over the last 10 s, and fitting
    absorption at the level
  E eaten and stacking, no line needed
Each rule runs all day and 09:30-11:00 only (when the owner mostly traded).

Money: the bot trades 10 MES = 1 ES ($50 a point), costs 10 x $1.24. The
owner's trades are mostly 1 ES; their actual net $ is shown.

Run from the repo root:   python -m research.bot_week_study            (add --reuse to skip rebuilding the books)
"""

from __future__ import annotations

import sys
from multiprocessing import Pool

import numpy as np
import pandas as pd

from research.journal_replay import es_context
from research.journal_study import OUT
from research.level_study import DERIVED, RAW
from research.level_test_book import (OWNER_MATCH_S, OWNER_MATCH_TICKS, TestReader, day_bursts, find_tests,
                                      marked_levels, owner_traded)
from research.level_test_study import owner_trades, summarise
from research.market_data import session_dates
from research.orderbook import build_book, check_book, load_mbo
from research.orderbook_study import typical_depth
from research.settings import load_settings

BOOK_DIR = RAW / "es_weeks_mbo"
TRADES_DIR = DERIVED / "es_weeks_trades"      # each day's trades, saved so exits can be re-run without the book
_S = load_settings()
_B = _S["book_study"]
TICK = _S["market"]["tick_size"]
STOP_TICKS = int(round(_B["check_stop_points"] / TICK))       # the basic 2-pt stop, 4-pt target exit
TARGET_TICKS = int(round(_B["check_target_points"] / TICK))
SLIP_TICKS = _B["slippage_ticks"]
MAX_TRADES = _B["max_trades_per_day"]
FLAT_ET = _B["flatten_et"]
CONTRACTS = _S["bot"]["contracts_per_trade"]                  # 10 MES = 1 ES
ES_POINT = _S["market"]["point_value_usd"] * CONTRACTS        # $ per point for the whole position
BURST_MIN = _B["burst_contracts_es"]
CTX = {}


def et(day: str, hhmm: str) -> float:
    return pd.Timestamp(f"{day} {hhmm}", tz="America/New_York").timestamp()


def one_day(row) -> pd.DataFrame | None:
    s, sessions, zones, offsets = CTX["s"], CTX["sessions"], CTX["zones"], CTX["offsets"]
    f = BOOK_DIR / f"{row['es']}_{row['day']}_mbo.dbn.zst"
    if not f.exists() or row["es"] not in offsets.index:
        return None
    day = row["day"]
    book = build_book(load_mbo(f), int(et(day, "08:00")), int(et(day, "16:00")))
    TRADES_DIR.mkdir(parents=True, exist_ok=True)
    book.trades.to_parquet(TRADES_DIR / f"{row['es']}_{day}.parquet", index=False)
    reader = TestReader(book, typical_depth(book), day_bursts(book, s["bot"]["absorption_burst_seconds"], 20))
    session = pd.Timestamp(session_dates(pd.Series([pd.Timestamp(et(day, "12:00"), unit="s", tz="UTC")])).iat[0])
    levels = marked_levels(sessions, zones, session, int(offsets[row["es"]]), et(day, "09:30"), et(day, "15:30"),
                           s["bot"]["recent_poc_lookback_sessions"])
    tests = find_tests(book, levels, et(day, "09:30"), et(day, "15:30"))
    if tests.empty:
        print(f"{day}: no tests", flush=True)
        return None
    reads = pd.DataFrame([reader.read(x.time, x.direction, x.lo, x.hi, x.edge) for x in tests.itertuples()])
    tests = pd.concat([tests.reset_index(drop=True), reads], axis=1)
    # exit of a trade taken at each test's entry: 2-pt stop, 4-pt target, flat at 15:59
    flat = et(day, FLAT_ET)
    ex = [trade_exit(reader, r.entry_time, r.entry_price, r.direction, flat) if r.turn == "turned" else (np.nan,) * 3
          for r in tests.itertuples()]
    tests[["points", "exit_time", "exit_how"]] = pd.DataFrame(ex, index=tests.index)
    tests["day"], tests["week"], tests["typical_size"] = day, row["week"], reader.typ
    chk = check_book(book)
    print(f"{day} {row['es']}: crossed {chk['crossed_seconds']} s, typical {reader.typ:.0f}, {len(levels)} levels, "
          f"{len(tests)} tests, {int((tests['turn'] == 'turned').sum())} entries", flush=True)
    return tests


def trade_exit(reader: TestReader, t_in: float, entry: int, d: int, flat: float) -> tuple:
    """Points per contract after slippage, exit time, and how it ended."""
    fill = entry + d * SLIP_TICKS
    i, j = np.searchsorted(reader.t, [t_in, flat], "right")
    move = (reader.px[i:j] - fill) * d
    a = np.nonzero(move >= TARGET_TICKS)[0]
    b = np.nonzero(move <= -STOP_TICKS)[0]
    a = a[0] if len(a) else np.inf
    b = b[0] if len(b) else np.inf
    if a == b == np.inf:
        k = j - 1
        return (reader.px[k] - fill) * d * TICK, float(reader.t[k]), "flat at close"
    if a < b:
        return TARGET_TICKS * TICK, float(reader.t[i + a]), "target"
    return -(STOP_TICKS + SLIP_TICKS) * TICK, float(reader.t[i + b]), "stop"


RULES = {
    "A every level test": lambda e: np.ones(len(e), bool),
    "B line held at the level": lambda e: (e["line_before"] >= 2) & (e["line_left_share"] >= 0.5),
    "C line held + eaten + stacking": lambda e: (e["line_before"] >= 2) & (e["line_left_share"] >= 0.5)
    & (e["eaten"] > 0) & (e["stacking"] > 0),
    "D full read (C + delta + absorption)": lambda e: (e["line_before"] >= 2) & (e["line_left_share"] >= 0.5)
    & (e["eaten"] > 0) & (e["stacking"] > 0) & (e["delta_10s"] > 0) & (e["absorption_max"] >= BURST_MIN),
    "E eaten + stacking, no line needed": lambda e: (e["eaten"] > 0) & (e["stacking"] > 0),
}


def reads_after_costs(tests: pd.DataFrame, cost: float) -> pd.DataFrame:
    """Every test that turned, traded with the bot's exits and slippage: results by read."""
    e = tests[tests["turn"] == "turned"].copy()
    local = pd.to_datetime(e["entry_time"], unit="s", utc=True).dt.tz_convert("America/New_York")
    conds = {name: np.asarray(rule(e), bool) for name, rule in RULES.items()}
    conds.update({"delta last 10 s the trade's way": (e["delta_10s"] > 0).to_numpy(),
                  "line before + stacking + delta (no hold check)": ((e["line_before"] >= 2) & (e["stacking"] > 0)
                                                                    & (e["delta_10s"] > 0)).to_numpy(),
                  "before 11:00": (local.dt.strftime("%H:%M") < "11:00").to_numpy(),
                  "POC tests": (e["sources"] == "POC").to_numpy(),
                  "zone tests": (e["sources"] == "zone").to_numpy(),
                  "you traded this test": e["owner_points"].notna().to_numpy()})
    rows = []
    for name, m in conds.items():
        g = e[m]
        rows.append(dict(read=name, tests=len(g), win_rate=(g["points"] > 0).mean() if len(g) else np.nan,
                         avg_points=g["points"].mean() if len(g) else np.nan,
                         usd_per_trade=g["points"].mean() * ES_POINT - cost if len(g) else np.nan))
    return pd.DataFrame(rows)


def bot_trades(tests: pd.DataFrame, rule, morning_only: bool) -> pd.DataFrame:
    if tests.empty or "entry_time" not in tests:
        return pd.DataFrame(columns=["points", "entry_time", "entry_price", "direction"])
    e = tests[tests["turn"] == "turned"].copy()
    e = e[np.asarray(rule(e), bool)]
    if morning_only:
        local = pd.to_datetime(e["entry_time"], unit="s", utc=True).dt.tz_convert("America/New_York")
        e = e[(local.dt.strftime("%H:%M") < "11:00").to_numpy(bool)]
    taken = []
    for day, g in e.sort_values("entry_time").groupby("day"):
        busy_until, n = -np.inf, 0
        for r in g.itertuples():
            if n >= MAX_TRADES or r.entry_time < busy_until:
                continue
            taken.append(r.Index)
            busy_until, n = r.exit_time, n + 1
    return e.loc[taken]


def matched_owner(bot: pd.DataFrame, owner: pd.DataFrame) -> int:
    n = 0
    for r in bot.itertuples():
        o = owner[(owner["direction"] == r.direction) & ((owner["t"] - r.entry_time).abs() <= OWNER_MATCH_S)
                  & ((owner["price"] - r.entry_price).abs() <= OWNER_MATCH_TICKS * 2)]
        n += len(o) > 0
    return n


def main() -> None:
    s = load_settings()
    plan = pd.read_csv(OUT / "es_week_price_plan.csv")
    have = [r for _, r in plan.iterrows() if (BOOK_DIR / f"{r['es']}_{r['day']}_mbo.dbn.zst").exists()]
    days = [r["day"] for r in have]
    missing = sorted(set(plan["day"]) - set(days))
    if missing:
        print("no book for", ", ".join(missing), "- left out on both sides", flush=True)
    owner = owner_trades()
    owner = owner[owner["day"].isin(days)].copy()          # same days for you and the bot
    owner["week"] = owner["day"].map(plan.set_index("day")["week"])
    saved = OUT / "level_tests_es_weeks.csv"
    if "--reuse" in sys.argv and saved.exists():            # skip rebuilding the books
        tests = pd.read_csv(saved)
    else:
        sessions, zones, offsets = es_context(s)
        CTX.update(s=s, sessions=sessions, zones=zones, offsets=offsets)
        with Pool(2) as pool:                    # big ES days need several GB each
            parts = pool.map(one_day, have)
        tests = pd.concat([p for p in parts if p is not None], ignore_index=True)
        tests["owner_points"] = np.nan
        for day, g in tests.groupby("day"):
            tests.loc[g.index, "owner_points"] = owner_traded(g, owner[owner["day"] == day]).to_numpy()
        tests.to_csv(saved, index=False)
    reads = summarise(tests, BURST_MIN)          # every test, not just the ones the bot could take
    reads.to_csv(OUT / "level_tests_es_weeks_summary.csv", index=False)

    cost = CONTRACTS * s["backtest"]["commission_per_contract_round_trip"]
    by_read = reads_after_costs(tests, cost)
    by_read.to_csv(OUT / "level_tests_es_weeks_after_costs.csv", index=False)
    rows = []
    for week, ow in owner.groupby("week"):
        rows.append(dict(week=week, who="you (journal)", trades=len(ow), wins=int((ow["points"] > 0).sum()),
                         win_rate=(ow["points"] > 0).mean(),
                         points=ow["points"].sum(), net_usd=ow["net_usd"].sum(), same_as_you=np.nan))
        wt = tests[tests["week"] == week]
        for name, rule in RULES.items():
            for morning in (False, True):
                b = bot_trades(wt, rule, morning)
                rows.append(dict(week=week, who=f"bot {name}{' (09:30-11:00)' if morning else ''}", trades=len(b),
                                 wins=int((b["points"] > 0).sum()),
                                 win_rate=(b["points"] > 0).mean() if len(b) else np.nan,
                                 points=b["points"].sum(), net_usd=b["points"].sum() * ES_POINT - cost * len(b),
                                 same_as_you=matched_owner(b, owner[owner["week"] == week])))
    res = pd.DataFrame(rows)
    res.to_csv(OUT / "bot_vs_owner_weeks.csv", index=False)
    total = res.groupby("who", sort=False).agg(trades=("trades", "sum"), wins=("wins", "sum"), points=("points", "sum"),
                                               net_usd=("net_usd", "sum"), same_as_you=("same_as_you", "sum"))
    total["win_rate"] = total["wins"] / total["trades"].clip(lower=1)
    total.to_csv(OUT / "bot_vs_owner_total.csv")
    pd.set_option("display.width", 250)
    print(reads.round(3).to_string())
    print(by_read.round(2).to_string())
    print(res.round(2).to_string())
    print("\nall five weeks")
    print(total.round(1).to_string())


if __name__ == "__main__":
    main()
