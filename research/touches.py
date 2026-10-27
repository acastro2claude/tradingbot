"""Tests of a price and whether they held (question 1).

A *test* of price L: price trades at L after having been at least `arm`
ticks away from it since it last traded at L. The side it came from decides
the setup: from above = support test (long), from below = resistance test
(short).

After the test we record, within `horizon` seconds and before the session
ends:
  - broke:  price traded `brk` ticks through L (the stop side)
  - mfe:    the furthest price moved back away from L before breaking (ticks)
A test *holds* at distance R if mfe >= R, i.e. price moved R back away
before breaking. Scoring several R values needs only the one mfe number.

  - stall_after: seconds until price first gets `stall` ticks away from L on
    either side (NaN if it doesn't within the horizon). Longer = price paused.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


class RangeExtremes:
    """Fast highest/lowest price between any two path positions (sparse table)."""

    def __init__(self, p: np.ndarray):
        self.hi = [np.asarray(p)]
        self.lo = [np.asarray(p)]
        k = 1
        while (1 << k) <= len(p):
            h, lo_, half = self.hi[-1], self.lo[-1], 1 << (k - 1)
            self.hi.append(np.maximum(h[:-half], h[half:]))
            self.lo.append(np.minimum(lo_[:-half], lo_[half:]))
            k += 1

    def _k(self, length):
        return np.floor(np.log2(np.maximum(length, 1))).astype(int)

    def max(self, left, right):
        """Highest price over positions left..right inclusive (arrays)."""
        left, right = np.asarray(left), np.asarray(right)
        k = self._k(right - left + 1)
        out = np.empty(len(left), dtype=np.int64)
        for kk in np.unique(k):
            m = k == kk
            out[m] = np.maximum(self.hi[kk][left[m]], self.hi[kk][right[m] - (1 << kk) + 1])
        return out

    def min(self, left, right):
        left, right = np.asarray(left), np.asarray(right)
        k = self._k(right - left + 1)
        out = np.empty(len(left), dtype=np.int64)
        for kk in np.unique(k):
            m = k == kk
            out[m] = np.minimum(self.lo[kk][left[m]], self.lo[kk][right[m] - (1 << kk) + 1])
        return out


def touch_events(p: np.ndarray, t: np.ndarray, prices, arm: int, brk: int,
                 horizon: float, stall: int | None = None) -> pd.DataFrame:
    """All tests of each price in `prices` along one session's path.

    p, t: path ticks and times (seconds) from research.paths.
    Returns one row per test: price, time, side (+1 support test from above,
    -1 resistance test from below), test_no (1 = first test that session),
    mfe (ticks), broke, break_after (seconds, NaN if no break), stall_after.
    """
    stall = brk if stall is None else stall
    p = np.asarray(p, dtype=np.int64)
    t = np.asarray(t, dtype=float)
    n = len(p)
    if n < 2:
        return _empty()
    if np.any(np.abs(np.diff(p)) != 1):
        raise ValueError("path must move one tick at a time (use research.paths)")
    ext = RangeExtremes(p)
    order = np.argsort(p, kind="stable")
    sp = p[order]

    def positions(price):
        a, b = np.searchsorted(sp, price, "left"), np.searchsorted(sp, price, "right")
        return order[a:b]

    out = []
    for L in np.unique(np.asarray(prices, dtype=np.int64)):
        idx = positions(L)
        if len(idx) == 0:
            continue
        prev = np.concatenate([[-1], idx[:-1]])
        seg_l, seg_r = prev + 1, idx - 1
        ok = seg_r >= seg_l
        if not ok.any():
            continue
        idx, seg_l, seg_r = idx[ok], seg_l[ok], seg_r[ok]
        side = np.where(p[seg_l] > L, 1, -1)          # where price was before reaching L
        far = np.where(side == 1, ext.max(seg_l, seg_r) - L, L - ext.min(seg_l, seg_r)) >= arm
        if not far.any():
            continue
        idx, side = idx[far], side[far]

        brk_pos = np.where(side == 1, _next_after(positions(L - brk), idx, n),
                           _next_after(positions(L + brk), idx, n))
        horizon_end = np.searchsorted(t, t[idx] + horizon, "right")
        broke = brk_pos < horizon_end
        end = np.minimum(brk_pos, horizon_end) - 1
        end = np.maximum(end, idx)
        mfe = np.where(side == 1, ext.max(idx, end) - L, L - ext.min(idx, end))
        break_after = np.where(broke, t[np.minimum(brk_pos, n - 1)] - t[idx], np.nan)
        leave_pos = np.minimum(_next_after(positions(L + stall), idx, n),
                               _next_after(positions(L - stall), idx, n))
        stall_after = np.where(leave_pos < horizon_end, t[np.minimum(leave_pos, n - 1)] - t[idx], np.nan)
        out.append(pd.DataFrame({
            "price": L, "pos": idx, "time": t[idx], "side": side,
            "test_no": np.arange(1, len(idx) + 1), "mfe": mfe,
            "broke": broke, "break_after": break_after, "stall_after": stall_after,
        }))
    return pd.concat(out, ignore_index=True) if out else _empty()


def _next_after(positions: np.ndarray, idx: np.ndarray, missing: int) -> np.ndarray:
    """First position after each idx, or `missing` if there is none."""
    padded = np.concatenate([positions, [missing]])
    return padded[np.searchsorted(positions, idx, "right")]


def _empty() -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype=d) for c, d in [
        ("price", "int64"), ("pos", "int64"), ("time", "float64"), ("side", "int64"),
        ("test_no", "int64"), ("mfe", "int64"), ("broke", "bool"), ("break_after", "float64"),
        ("stall_after", "float64")]})


def outcome(mfe, broke, hold_ticks: int) -> np.ndarray:
    """'hold', 'break' or 'open' (neither happened in time) for a hold distance."""
    mfe = np.asarray(mfe)
    broke = np.asarray(broke)
    return np.where(mfe >= hold_ticks, "hold", np.where(broke, "break", "open"))
