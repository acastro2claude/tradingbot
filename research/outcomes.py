"""Did price go the expected way? Outcomes for events at a time, price and direction.

For an event expecting price to go DOWN from reference price P (a short):
  - broke:  price traded `brk` ticks ABOVE P (the stop side) before the horizon
  - mfe:    the furthest price fell below P before breaking (ticks)
Mirror for UP. The event "worked" at target R if mfe >= R.

Also measures the hold check from STRATEGY.md section 2: the furthest price
went past P in the aggressors' direction within the first `check` seconds.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from research.touches import RangeExtremes


def event_outcomes(p: np.ndarray, t: np.ndarray, times, prices, directions, brk: int,
                   horizon: float, checks=()) -> pd.DataFrame:
    """Outcomes along one session's path (research.paths).

    times: event times (s), measured from the first path point at or after each time.
    prices: reference prices (ticks). directions: -1 expect down, +1 expect up.
    checks: seconds; adds `against_<s>s`, the most price went against the
    expected direction (past P) within that many seconds, in ticks (0 if never).
    """
    p = np.asarray(p, dtype=np.int64)
    t = np.asarray(t, dtype=float)
    times = np.asarray(times, dtype=float)
    P = np.asarray(prices, dtype=np.int64)
    d = np.asarray(directions, dtype=np.int64)
    n = len(p)
    ext = RangeExtremes(p)
    i0 = np.minimum(np.searchsorted(t, times, "left"), n - 1)
    h_end = np.searchsorted(t, times + horizon, "right")            # first index past the horizon

    order = np.argsort(p, kind="stable")
    sp = p[order]

    def next_at(price, start):
        """First path index >= start where price trades at `price` (path moves one tick at a time)."""
        out = np.full(len(price), n)
        for k, (pr, st) in enumerate(zip(price, start)):
            a, b = np.searchsorted(sp, pr, "left"), np.searchsorted(sp, pr, "right")
            pos = order[a:b]
            j = np.searchsorted(pos, st, "left")
            if j < len(pos):
                out[k] = pos[j]
        return out

    stop_px = P - d * brk                       # short (d=-1): P + brk
    brk_pos = next_at(stop_px, i0)
    broke = brk_pos < h_end
    end = np.maximum(np.minimum(brk_pos, h_end) - 1, i0)
    mfe = np.where(d == -1, P - ext.min(i0, end), ext.max(i0, end) - P)
    out = pd.DataFrame({"mfe": mfe, "broke": broke,
                        "break_after": np.where(broke, t[np.minimum(brk_pos, n - 1)] - times, np.nan)})
    for c in checks:
        c_end = np.maximum(np.searchsorted(t, times + c, "right") - 1, i0)
        against = np.where(d == -1, ext.max(i0, c_end) - P, P - ext.min(i0, c_end))
        out[f"against_{c:g}s"] = np.maximum(against, 0)
    return out
