"""Volume profiles: volume traded at each price.

From trades the profile is exact. From 1-minute bars each bar's volume is
spread evenly over every tick between its low and high (the usual
approximation; the check days measure how close it gets).
"""

from __future__ import annotations

import numpy as np


def bar_volume_at_price(low, high, volume) -> tuple[np.ndarray, np.ndarray]:
    """Spread each bar's volume evenly over its low..high ticks. Returns (ticks, volume)."""
    low = np.asarray(low, dtype=np.int64)
    high = np.asarray(high, dtype=np.int64)
    volume = np.asarray(volume, dtype=float)
    if len(low) == 0:
        return np.array([], dtype=np.int64), np.array([], dtype=float)
    width = high - low + 1
    if (width < 1).any():
        raise ValueError("bar with high below low")
    bar_idx = np.repeat(np.arange(len(low)), width)
    step = np.arange(width.sum()) - np.repeat(np.cumsum(width) - width, width)
    tick = low[bar_idx] + step
    share = (volume / width)[bar_idx]
    lo = tick.min()
    vol = np.bincount(tick - lo, weights=share)
    ticks = np.arange(lo, lo + len(vol))
    keep = vol > 0
    return ticks[keep], vol[keep]


def trade_volume_at_price(price, size) -> tuple[np.ndarray, np.ndarray]:
    price = np.asarray(price, dtype=np.int64)
    size = np.asarray(size, dtype=float)
    lo = price.min()
    vol = np.bincount(price - lo, weights=size)
    ticks = np.arange(lo, lo + len(vol))
    keep = vol > 0
    return ticks[keep], vol[keep]


def poc(ticks, vol) -> int:
    """Price with the most volume. Ties go to the one nearest the volume-weighted average price."""
    ticks = np.asarray(ticks)
    vol = np.asarray(vol, dtype=float)
    top = np.flatnonzero(vol >= vol.max() - 1e-9)
    if len(top) == 1:
        return int(ticks[top[0]])
    vwap = (ticks * vol).sum() / vol.sum()
    return int(ticks[top[np.argmin(np.abs(ticks[top] - vwap))]])


def value_area(ticks, vol, share: float = 0.70) -> tuple[int, int]:
    """(low, high) of the range around the POC holding `share` of the volume.

    Starts at the POC and keeps adding whichever neighbouring price (above or
    below) has more volume until the share is reached.
    """
    ticks = np.asarray(ticks, dtype=np.int64)
    vol = np.asarray(vol, dtype=float)
    lo_t = ticks.min()
    dense = np.zeros(ticks.max() - lo_t + 1)
    dense[ticks - lo_t] = vol
    i = j = poc(ticks, vol) - lo_t
    total, target = dense[i], share * dense.sum()
    while total < target - 1e-9:
        up = dense[j + 1] if j + 1 < len(dense) else -1.0
        down = dense[i - 1] if i > 0 else -1.0
        if up >= down:
            j += 1
            total += up
        else:
            i -= 1
            total += down
    return int(i + lo_t), int(j + lo_t)


class ProfileMatrix:
    """Daily profiles on one shared price grid: rows = sessions, columns = ticks.

    Lets long-term profiles be summed quickly with running totals.
    """

    def __init__(self, sessions: list, tick_lo: int, tick_hi: int):
        self.sessions = list(sessions)
        self.row = {s: i for i, s in enumerate(self.sessions)}
        self.tick_lo = int(tick_lo)
        self.data = np.zeros((len(self.sessions), int(tick_hi) - int(tick_lo) + 1))
        self._cum = None

    def add(self, session, ticks, vol) -> None:
        self.data[self.row[session], np.asarray(ticks) - self.tick_lo] += vol
        self._cum = None

    def profile(self, session) -> tuple[np.ndarray, np.ndarray]:
        r = self.data[self.row[session]]
        keep = np.flatnonzero(r > 0)
        return keep + self.tick_lo, r[keep]

    def sum_rows(self, rows: np.ndarray) -> np.ndarray:
        """Total volume at each tick over the given row numbers."""
        return self.data[np.asarray(rows, dtype=int)].sum(axis=0)
