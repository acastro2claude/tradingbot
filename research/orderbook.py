"""Rebuild the full MES order book from Databento MBO (order by order) data.

Each day's file starts at 00:00 UTC with Databento's snapshot of every resting
order, then lists every change: A add, C cancel (size = amount cancelled),
M modify (new price and new total size), R clear the book. F (fill) and T
(trade) records do not change the book by themselves: CME follows each fill
with a cancel or modify of the resting order, and that is what moves the book.

From the order changes this builds, for a time window:
  - depth: resting contracts at every price, bid and ask, at the end of each
    second (what Bookmap's heatmap draws)
  - flows: per second, price and side, contracts added, pulled (cancelled
    or moved away without trading) and filled
  - trades: every trade with its aggressor side

Prices are in ticks (0.25 points). Times are UTC epoch seconds.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

TICK_NANO = 250_000_000          # Databento prices are fixed point, 1e-9 units
UNDEF_PRICE = np.iinfo(np.int64).max
ADD, PULL, FILL, CLEAR = 0, 1, 2, 3
BID, ASK = 0, 1


@dataclass
class Book:
    t0: int                      # first second of the grid (epoch seconds)
    lo: int                      # lowest price in the grid (ticks)
    bid: np.ndarray              # [second, price - lo] resting bid contracts at the end of that second
    ask: np.ndarray
    flows: pd.DataFrame          # sec, side, price, added, pulled, filled
    trades: pd.DataFrame         # t (s), price, size, side ('B' buy aggressor, 'A' sell aggressor)

    def row(self, t: float) -> int:
        """Grid row for the book as it stood just before time t (end of the previous full second)."""
        return int(np.floor(t)) - self.t0 - 1

    def best(self, r: int) -> tuple[int, int]:
        """Best bid and ask (ticks) in grid row r (-1 if that side is empty)."""
        b = np.nonzero(self.bid[r] > 0)[0]
        a = np.nonzero(self.ask[r] > 0)[0]
        return (self.lo + b[-1] if len(b) else -1, self.lo + a[0] if len(a) else -1)


def load_mbo(path: Path) -> np.ndarray:
    import databento as db
    return db.DBNStore.from_file(path).to_ndarray()


def book_changes(a: np.ndarray) -> pd.DataFrame:
    """Every change to resting size: event index, side, price, signed contracts, kind.

    A fill reported for an order marks the next reduction of that order (up to
    the filled amount) as FILL; any other reduction is PULL.
    """
    action = a["action"].tolist()
    side = a["side"].tolist()
    price = a["price"].tolist()
    size = a["size"].tolist()
    oid = a["order_id"].tolist()
    orders: dict[int, list] = {}         # order id -> [side, price ticks, size, pending fill]
    out_i, out_s, out_p, out_q, out_k = [], [], [], [], []

    def emit(i, s, p, q, k):
        out_i.append(i); out_s.append(s); out_p.append(p); out_q.append(q); out_k.append(k)

    def reduce(i, o, q):
        f = min(q, o[3])
        if f:
            emit(i, o[0], o[1], -f, FILL)
            o[3] -= f
        if q - f:
            emit(i, o[0], o[1], -(q - f), PULL)

    for i in range(len(action)):
        act = action[i]
        if act == b"A":
            if price[i] == UNDEF_PRICE or side[i] == b"N":
                continue
            s, p = (BID if side[i] == b"B" else ASK), price[i] // TICK_NANO
            orders[oid[i]] = [s, p, size[i], 0]
            emit(i, s, p, size[i], ADD)
        elif act == b"C":
            o = orders.get(oid[i])
            if o is None:
                continue
            q = min(size[i], o[2])
            reduce(i, o, q)
            o[2] -= q
            if o[2] <= 0:
                del orders[oid[i]]
        elif act == b"M":
            o = orders.get(oid[i])
            if price[i] == UNDEF_PRICE:
                continue
            p = price[i] // TICK_NANO
            if o is None:                                   # modify of an order we never saw: treat as add
                s = BID if side[i] == b"B" else ASK
                orders[oid[i]] = [s, p, size[i], 0]
                emit(i, s, p, size[i], ADD)
                continue
            if p != o[1]:                                   # moved: leaves the old price, joins the new one
                reduce(i, o, o[2])
                o[1], o[2], o[3] = p, size[i], 0
                emit(i, o[0], p, size[i], ADD)
            elif size[i] > o[2]:
                emit(i, o[0], p, size[i] - o[2], ADD)
                o[2] = size[i]
            elif size[i] < o[2]:
                reduce(i, o, o[2] - size[i])
                o[2] = size[i]
        elif act == b"F":
            o = orders.get(oid[i])
            if o is not None:
                o[3] += size[i]
        elif act == b"R":
            for o in orders.values():
                emit(i, o[0], o[1], -o[2], CLEAR)
            orders.clear()
    return pd.DataFrame(dict(i=np.array(out_i, dtype=np.int64), side=np.array(out_s, dtype=np.int8),
                             price=np.array(out_p, dtype=np.int64), qty=np.array(out_q, dtype=np.int64),
                             kind=np.array(out_k, dtype=np.int8)))


def build_book(a: np.ndarray, start_s: int, end_s: int, pad_points: float = 40.0) -> Book:
    """Depth grid, flows and trades from start_s to end_s (UTC epoch seconds)."""
    ch = book_changes(a)
    ts = a["ts_recv"].astype(np.int64)
    ch["t"] = ts[ch["i"].to_numpy()] // 1_000_000_000

    is_trade = a["action"] == b"T"
    tr = pd.DataFrame(dict(t=ts[is_trade] / 1e9, price=a["price"][is_trade] // TICK_NANO,
                           size=a["size"][is_trade].astype(np.int64),
                           side=a["side"][is_trade].astype(str)))
    win = tr[(tr["t"] >= start_s) & (tr["t"] < end_s)]
    pad = int(round(pad_points * 4))
    lo = int(win["price"].min()) - pad
    hi = int(win["price"].max()) + pad
    n_s, n_p = end_s - start_s, hi - lo + 1

    grids = []
    for s in (BID, ASK):
        c = ch[(ch["side"] == s) & (ch["price"] >= lo) & (ch["price"] <= hi)]
        g = np.zeros((n_s, n_p), dtype=np.int64)
        before = c[c["t"] < start_s]
        base = np.bincount(before["price"] - lo, weights=before["qty"], minlength=n_p)
        inside = c[(c["t"] >= start_s) & (c["t"] < end_s)]
        np.add.at(g, (inside["t"].to_numpy() - start_s, inside["price"].to_numpy() - lo), inside["qty"].to_numpy())
        g = np.cumsum(g, axis=0) + base.astype(np.int64)
        grids.append(g.astype(np.int32))

    f = ch[(ch["t"] >= start_s) & (ch["t"] < end_s) & (ch["kind"] != CLEAR)].copy()
    f["sec"] = f["t"] - start_s
    f["added"] = np.where(f["kind"] == ADD, f["qty"], 0)
    f["pulled"] = np.where(f["kind"] == PULL, -f["qty"], 0)
    f["filled"] = np.where(f["kind"] == FILL, -f["qty"], 0)
    flows = f.groupby(["sec", "side", "price"], as_index=False)[["added", "pulled", "filled"]].sum()
    return Book(t0=start_s, lo=lo, bid=grids[0], ask=grids[1], flows=flows, trades=tr.reset_index(drop=True))


def check_book(book: Book) -> dict:
    """Sanity checks: no negative depth, and the book never crossed (best bid below best ask)."""
    neg = int((book.bid < 0).sum() + (book.ask < 0).sum())
    rows = np.arange(len(book.bid))
    has_b, has_a = (book.bid > 0).any(axis=1), (book.ask > 0).any(axis=1)
    bb = np.where(has_b, book.bid.shape[1] - 1 - np.argmax((book.bid > 0)[:, ::-1], axis=1), -1)
    ba = np.where(has_a, np.argmax(book.ask > 0, axis=1), 10**9)
    crossed = int(((bb >= ba) & has_b & has_a).sum())
    spread = (ba - bb)[has_b & has_a]
    return dict(seconds=len(rows), negative_cells=neg, crossed_seconds=crossed,
                median_spread_ticks=float(np.median(spread)) if len(spread) else np.nan)
