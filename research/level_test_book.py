"""Tests of marked levels, read the way the owner reads Bookmap (build step 3, second pass).

The owner's read (STRATEGY.md section 2, Oct 2026), long example (short = mirror):
  1. a marked level: a recent uncovered POC (1e) or a demand zone (1b), best when both
  2. a line of large resting bids at or around it before price gets there
  3. price tests it: do the bids get eaten, or do even more bids join?
  4. delta skews to buyers as the bids get eaten; buyer absorption markers; more bids come in
  5. enter once price turns
Lines away from marked levels don't count: they're usually pulled before price arrives.

For every test of a marked level in the book's window, using only what was known
at each moment:
  - test start: price trades at the level after being at least 5 points away
  - entry ("turn"): the first moment price trades TURN_TICKS back the trade's way from
    the most extreme price of the test, within MAX_TEST_S, before trading 3 points
    through the level (if it breaks first, there is no entry)
  - line before: the biggest resting size on the holding side (bids for a long)
    at any price in the level area, over the 5 minutes before the test, as a
    multiple of the day's typical size per price; and what was left of it when
    price arrived (pulled before arrival)
  - during the test (start to entry), in the level area on the holding side:
    contracts filled ("eaten"), added and pulled; more added than pulled = stacking
  - delta over the 10 seconds before the entry, in the trade's direction (owner: "starts to skew")
  - absorption bursts of the fitting colour in the level area, from 1 minute
    before the test to the entry
  - outcome from the entry (market order at the bid/ask): 4 points in favour
    before 2 against (the owner's median win and loss), within 30 minutes
  - whether the owner traded it: an owner entry in the same direction within
    10 minutes of the test start, within 1 point of the level area

Levels come from ES 1-minute bars (POCs and 15-minute zones, back-adjusted for
rolls) and are shifted onto the book's contract.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from research.absorption import find_bursts
from research.orderbook import ASK, BID, Book
from research.paths import trade_path
from research.recent_poc_study import recent_poc_labels
from research.touches import touch_events

AREA_TICKS = 4            # proposed: "at or around" a level = within 1 point of it (zones: of its edges)
ARM_TICKS = 20            # a test needs price to have been 5 points away first (level study setting)
BREAK_TICKS = 12          # 3 points through the level = the test failed (stop buffer)
TURN_TICKS = 4            # proposed: entry once price trades 1 point back from the test's extreme (2 ticks fired on bid/ask bounce)
MAX_TEST_S = 300          # the turn must come within 5 minutes of the test start
LINE_LOOKBACK_S = 300
DELTA_S = 10
TARGET_TICKS, STOP_TICKS, HORIZON_S = 16, 8, 1800
OWNER_MATCH_S, OWNER_MATCH_TICKS = 600, 4


def marked_levels(sessions: pd.DataFrame, zones: pd.DataFrame, session, offset: int, t0: float, t1: float,
                  lookback: int) -> pd.DataFrame:
    """Levels on the book's contract for one session window.

    Rows: source ("POC" or "zone"), direction (+1 long from above, -1 short from below,
    0 = both for POCs), edge (the price a test starts at), lo, hi (the level area before padding).
    """
    rows = []
    usable = list(sessions.index[sessions["use"]])
    if session in usable and usable.index(session) >= lookback:
        rec = recent_poc_labels(sessions, pd.Index([session]), lookback)
        for p in rec.loc[~rec["covered"], "price"]:
            p = int(p) - offset
            rows.append(dict(source="POC", direction=0, edge=p, lo=p, hi=p))
    z = zones[(zones["created"] <= t1) & (zones["dies"] > t0)]
    for zr in z.itertuples():
        lo, hi = int(zr.low) - offset, int(zr.high) - offset
        if zr.kind == "demand":
            rows.append(dict(source="zone", direction=1, edge=hi, lo=lo, hi=hi, created=zr.created, dies=zr.dies))
        else:
            rows.append(dict(source="zone", direction=-1, edge=lo, lo=lo, hi=hi, created=zr.created, dies=zr.dies))
    return pd.DataFrame(rows)


def _hold_depth(book: Book, side: int, r0: int, r1: int, lo: int, hi: int) -> np.ndarray:
    g = book.bid if side == BID else book.ask
    a, b = max(lo - book.lo, 0), min(hi - book.lo + 1, g.shape[1])
    return g[max(r0, 0): r1 + 1, a:b]


class TestReader:
    """Reads the owner's signals at tests of marked levels on one day's book."""

    def __init__(self, book: Book, typ: float, bursts: pd.DataFrame):
        self.book, self.typ = book, typ
        tr = book.trades
        self.t = tr["t"].to_numpy()
        self.px = tr["price"].to_numpy().astype(np.int64)
        self.signed = np.where(tr["side"] == "B", tr["size"], np.where(tr["side"] == "A", -tr["size"], 0))
        self.cum = np.concatenate([[0], np.cumsum(self.signed)])
        self.bursts = bursts
        f = book.flows
        self.f_sec, self.f_side, self.f_price = f["sec"].to_numpy(), f["side"].to_numpy(), f["price"].to_numpy()
        self.f_vals = f[["added", "pulled", "filled"]].to_numpy()

    def flows(self, r0: int, r1: int, side: int, lo: int, hi: int) -> np.ndarray:
        """Added, pulled, filled on one side, prices lo..hi, grid rows r0..r1 inclusive."""
        i, j = np.searchsorted(self.f_sec, [r0, r1 + 1])
        m = (self.f_side[i:j] == side) & (self.f_price[i:j] >= lo) & (self.f_price[i:j] <= hi)
        return self.f_vals[i:j][m].sum(axis=0)

    def delta(self, a: float, b: float) -> int:
        i, j = np.searchsorted(self.t, [a, b], "left")
        return int(self.cum[j] - self.cum[i])

    def turn(self, t0: float, d: int, edge: int) -> tuple[float | None, str]:
        """Entry time once price turns TURN_TICKS back from the test extreme, or None if it breaks first."""
        i, j = np.searchsorted(self.t, [t0, t0 + MAX_TEST_S], "left")
        p = self.px[i:j]
        if len(p) == 0:
            return None, "no trades"
        ext = np.minimum.accumulate(p) if d == 1 else np.maximum.accumulate(p)
        broke = np.nonzero((edge - p) * d >= BREAK_TICKS)[0]
        turned = np.nonzero((p - ext) * d >= TURN_TICKS)[0]
        k_b = broke[0] if len(broke) else np.inf
        k_t = turned[0] if len(turned) else np.inf
        if k_t < k_b:
            return float(self.t[i + k_t]), "turned"
        return None, "broke first" if k_b < np.inf else "no turn in time"

    def outcome(self, t: float, entry: int, d: int) -> str:
        i, j = np.searchsorted(self.t, [t, t + HORIZON_S], "right")
        move = (self.px[i:j] - entry) * d
        a = np.nonzero(move >= TARGET_TICKS)[0]
        b = np.nonzero(move <= -STOP_TICKS)[0]
        a = a[0] if len(a) else np.inf
        b = b[0] if len(b) else np.inf
        return "neither" if a == b == np.inf else ("target" if a < b else "stop")

    def read(self, t0: float, d: int, lo: int, hi: int, edge: int) -> dict:
        book = self.book
        side = BID if d == 1 else ASK
        a_lo, a_hi = lo - AREA_TICKS, hi + AREA_TICKS
        r_start = book.row(t0)
        before = _hold_depth(book, side, r_start - LINE_LOOKBACK_S, r_start, a_lo, a_hi)
        line_before = before.max() if before.size else 0
        line_now = before[-1].max() if before.size else 0
        out = dict(line_before=line_before / self.typ, line_at_arrival=line_now / self.typ,
                   line_left_share=line_now / line_before if line_before else np.nan)
        t_in, how = self.turn(t0, d, edge)
        out["turn"] = how
        if t_in is None:
            return out
        r_in = book.row(t_in)
        added, pulled, filled = self.flows(r_start + 1, r_in, side, a_lo, a_hi)
        depth_in = _hold_depth(book, side, r_in, r_in, a_lo, a_hi)
        bb, ba = book.best(r_in)
        entry = ba if d == 1 else bb
        b = self.bursts
        fit = b[(b["direction"] == d) & (b["price"] >= a_lo) & (b["price"] <= a_hi)
                & (b["end_time"] >= t0 - 60) & (b["end_time"] < t_in)]
        out.update(entry_time=t_in, entry_price=entry, test_seconds=t_in - t0,
                   eaten=int(filled), added=int(added), pulled=int(pulled),
                   stacking=int(added) - int(pulled),
                   hold_depth_at_entry=int(depth_in.sum()) if depth_in.size else 0,
                   delta_10s=self.delta(t_in - DELTA_S, t_in) * d,
                   delta_test=self.delta(t0, t_in) * d,
                   absorption_max=int(fit["contracts"].max()) if len(fit) else 0,
                   absorption_count=len(fit),
                   outcome_2_4=self.outcome(t_in, entry, d))
        return out


def day_bursts(book: Book, window: float, min_size: int) -> pd.DataFrame:
    tr = book.trades
    b = find_bursts(tr["t"].to_numpy(), tr["price"].to_numpy().astype(np.int64), tr["size"].to_numpy(),
                    tr["side"].to_numpy(), window, min_size)
    b["direction"] = np.where(b["colour"] == "purple", -1, 1)
    return b


def find_tests(book: Book, levels: pd.DataFrame, start: float, end: float) -> pd.DataFrame:
    """Tests of the marked levels between start and end (book trades), one row per test."""
    tr = book.trades[(book.trades["t"] >= start - 3600) & (book.trades["t"] < end)]
    p, t = trade_path(tr["price"].to_numpy(), tr["t"].to_numpy())
    if len(p) < 2 or levels.empty:
        return pd.DataFrame()
    ev = touch_events(p, t, np.unique(levels["edge"].to_numpy()), ARM_TICKS, BREAK_TICKS, HORIZON_S)
    ev = ev[(ev["time"] >= start) & (ev["time"] < end)]
    rows = []
    for e in ev.itertuples():
        for lv in levels[levels["edge"] == e.price].itertuples():
            if lv.direction not in (0, e.side):
                continue
            if lv.source == "zone" and not (lv.created <= e.time < lv.dies):
                continue
            rows.append(dict(time=e.time, direction=e.side, edge=e.price, lo=lv.lo, hi=lv.hi, source=lv.source))
    if not rows:
        return pd.DataFrame()
    out = pd.DataFrame(rows)
    # one test per moment and direction; note when a POC and a zone line up
    agg = out.groupby(["time", "direction", "edge"]).agg(lo=("lo", "min"), hi=("hi", "max"),
                                                         sources=("source", lambda s: "+".join(sorted(set(s)))))
    return agg.reset_index()


def owner_traded(tests: pd.DataFrame, owner: pd.DataFrame) -> pd.Series:
    """Owner entry (time s, direction, price ticks) in the same direction near the level soon after the test."""
    hit = []
    for x in tests.itertuples():
        o = owner[(owner["direction"] == x.direction) & (owner["t"] >= x.time) & (owner["t"] <= x.time + OWNER_MATCH_S)
                  & (owner["price"] >= x.lo - AREA_TICKS - OWNER_MATCH_TICKS)
                  & (owner["price"] <= x.hi + AREA_TICKS + OWNER_MATCH_TICKS)]
        hit.append(o["points"].iloc[0] if len(o) else np.nan)
    return pd.Series(hit, index=tests.index)
