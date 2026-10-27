"""Price paths: every tick price passed through, in order, with the time it got there.

From trades the path is the real sequence of trade prices. From 1-minute bars
the order inside each bar is assumed: open, then the extreme nearer the open
side (low first on an up bar, high first on a down bar), then the other
extreme, then the close, at 0, 20, 40 and 60 seconds into the bar. That is the
usual assumption for bar data and the check days measure how much it matters.

Jumps (a gap between two bars, or a trade several ticks away from the last)
still pass through every tick in between, all stamped with the time of the jump.
"""

from __future__ import annotations

import numpy as np


def _expand(vertex_price: np.ndarray, vertex_time: np.ndarray, jump: np.ndarray
            ) -> tuple[np.ndarray, np.ndarray]:
    """Fill in every tick between consecutive vertices.

    `jump[i]` says whether the move from vertex i to i+1 happens all at once
    (ticks get the end time) or steadily (times spread evenly).
    """
    p = np.asarray(vertex_price, dtype=np.int64)
    t = np.asarray(vertex_time, dtype=float)
    d = np.diff(p)
    n = np.abs(d)
    seg = np.repeat(np.arange(len(d)), n)
    k = np.arange(n.sum()) - np.repeat(np.cumsum(n) - n, n) + 1   # 1..n within segment
    sign = np.sign(d)[seg]
    price = p[seg] + sign * k
    frac = k / n[seg]
    t0, t1 = t[seg], t[seg + 1]
    time = np.where(jump[seg], t1, t0 + frac * (t1 - t0))
    return np.concatenate([[p[0]], price]), np.concatenate([[t[0]], time])


def bar_path(open_, high, low, close, start_seconds, bar_seconds: float = 60.0
             ) -> tuple[np.ndarray, np.ndarray]:
    """Path through a sequence of bars. Times are in seconds (any origin)."""
    o, h, lo, c = (np.asarray(x, dtype=np.int64) for x in (open_, high, low, close))
    st = np.asarray(start_seconds, dtype=float)
    up = c >= o
    first = np.where(up, lo, h)
    second = np.where(up, h, lo)
    vp = np.column_stack([o, first, second, c]).ravel()
    vt = (st[:, None] + np.array([0.0, 1 / 3, 2 / 3, 1.0]) * bar_seconds).ravel()
    jump = np.zeros(len(vp) - 1, dtype=bool)
    jump[3::4] = True   # close of one bar to open of the next
    return _expand(vp, vt, jump)


def trade_path(price, seconds) -> tuple[np.ndarray, np.ndarray]:
    """Path through a sequence of trades (repeated prices collapse to one point)."""
    p = np.asarray(price, dtype=np.int64)
    t = np.asarray(seconds, dtype=float)
    keep = np.concatenate([[True], np.diff(p) != 0])
    p, t = p[keep], t[keep]
    return _expand(p, t, np.ones(max(len(p) - 1, 0), dtype=bool))
