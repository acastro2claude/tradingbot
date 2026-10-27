"""Supply and demand zones (STRATEGY.md 1b), as the owner draws them.

A zone: price chops in an area on a higher-timeframe chart, then a candle
moves sharply away. Up = demand zone below, down = supply zone above.

A zone stops counting when price blows past it (a candle on its timeframe
closes beyond the far side), when price chops in it for too long (every
1-minute close inside for more than an hour), or after about a month.

Prices are adjusted ticks (see research.market_data), times are epoch seconds.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from research.market_data import epoch_seconds


def make_candles(bars: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """Higher-timeframe candles from 1-minute bars (columns *_adj, volume, ts, session).

    Clock-aligned (a 30-minute candle covers :00-:30 or :30-:00) and never
    spanning two sessions.
    """
    start = bars["ts"].dt.floor(f"{minutes}min")
    g = bars.groupby([bars["session"], start], sort=True)
    c = g.agg(open=("open_adj", "first"), high=("high_adj", "max"), low=("low_adj", "min"),
              close=("close_adj", "last"), volume=("volume", "sum")).reset_index()
    c.columns = ["session", "start_ts", "open", "high", "low", "close", "volume"]
    c["start"] = epoch_seconds(c["start_ts"])
    c["end"] = c["start"] + minutes * 60
    return c.drop(columns="start_ts")


DRAWINGS = ("last chop candle body", "last chop candle wicks", "whole chop bodies", "whole chop wicks")


def find_zones(candles: pd.DataFrame, base_min: int, ratio: float,
               drawing: str = "last chop candle body") -> pd.DataFrame:
    """Zones from chop (base_min+ candles all through one common price) then a sharp candle.

    Sharp: range >= ratio x the last chop candle's range, closing beyond the chop.
    Drawing (where the zone's edges go):
      "last chop candle body":  open to close of the candle just before the move
      "last chop candle wicks": low to high of that candle
      "whole chop bodies":      lowest to highest open/close across all the chop candles
      "whole chop wicks":       lowest low to highest high of all the chop candles
    """
    if drawing not in DRAWINGS:
        raise ValueError(f"unknown zone drawing {drawing!r}; use one of {DRAWINGS}")
    lo, hi, op, cl = (candles[k].to_numpy() for k in ("low", "high", "open", "close"))
    rng = hi - lo
    rows = []
    for i in range(base_min, len(candles)):
        j = i - 1
        top, bottom = hi[j], lo[j]          # common price band of the chop so far
        while j - 1 >= 0 and max(bottom, lo[j - 1]) <= min(top, hi[j - 1]):
            j -= 1
            top, bottom = min(top, hi[j]), max(bottom, lo[j])
        if i - j < base_min:
            continue
        if rng[i] < ratio * max(rng[i - 1], 1):
            continue
        chop_lo, chop_hi = lo[j:i].min(), hi[j:i].max()
        if cl[i] > chop_hi:
            kind = "demand"
        elif cl[i] < chop_lo:
            kind = "supply"
        else:
            continue
        if drawing == "last chop candle body":
            z_lo, z_hi = min(op[i - 1], cl[i - 1]), max(op[i - 1], cl[i - 1])
        elif drawing == "last chop candle wicks":
            z_lo, z_hi = lo[i - 1], hi[i - 1]
        elif drawing == "whole chop bodies":
            z_lo, z_hi = np.minimum(op[j:i], cl[j:i]).min(), np.maximum(op[j:i], cl[j:i]).max()
        else:
            z_lo, z_hi = chop_lo, chop_hi
        rows.append(dict(kind=kind, low=int(z_lo), high=int(z_hi), base_candles=i - j,
                         base_start=candles["start"].iat[j], created=candles["end"].iat[i],
                         created_session=candles["session"].iat[i]))
    return pd.DataFrame(rows, columns=["kind", "low", "high", "base_candles", "base_start",
                                       "created", "created_session"])


def zone_lifetimes(zones: pd.DataFrame, candles: pd.DataFrame, minute_bars: pd.DataFrame,
                   chop_minutes: float, lookback_sessions: int, session_order: list) -> pd.DataFrame:
    """Add `dies` (epoch seconds) and `why` (blown past / chopped in / expired / data ended)."""
    z = zones.copy()
    c_start, c_end, c_close = (candles[k].to_numpy() for k in ("start", "end", "close"))
    m_t = epoch_seconds(minute_bars["ts"])
    m_close = minute_bars["close_adj"].to_numpy()
    pos = {s: i for i, s in enumerate(session_order)}
    first = minute_bars.groupby("session")["ts"].min()
    first_bar = pd.Series(epoch_seconds(first), index=first.index)
    session_open = [first_bar.get(s, np.inf) for s in session_order]

    dies, why = [], []
    for zr in z.itertuples(index=False):
        k = pos[zr.created_session] + lookback_sessions + 1
        expiry = session_open[k] if k < len(session_order) else np.inf

        a = np.searchsorted(c_start, zr.created, "left")
        b = np.searchsorted(c_start, expiry, "left")
        far = c_close[a:b] < zr.low if zr.kind == "demand" else c_close[a:b] > zr.high
        blow = c_end[a + np.argmax(far)] if far.any() else np.inf

        a = np.searchsorted(m_t, zr.created, "left")
        b = np.searchsorted(m_t, min(expiry, blow), "left")
        t = m_t[a:b]
        inside = (m_close[a:b] >= zr.low) & (m_close[a:b] <= zr.high)
        chop = np.inf
        if inside.any():
            run = np.cumsum(~inside)                     # same number = same stay inside
            first_inside = pd.Series(np.where(inside, t, np.inf))
            starts = first_inside.groupby(run).transform("min").to_numpy()
            long_stay = inside & (t + 60 - starts > chop_minutes * 60)
            if long_stay.any():
                chop = starts[np.argmax(long_stay)] + chop_minutes * 60

        end = min(expiry, blow, chop)
        dies.append(end)
        why.append("data ended" if end == np.inf else
                   "blown past" if end == blow else "chopped in" if end == chop else "expired")
    z["dies"] = dies
    z["why"] = why
    return z


def label_tests(events: pd.DataFrame, zones: pd.DataFrame) -> pd.DataFrame:
    """For each price test: in a live zone that matches its side, at that zone's near edge, in any live zone.

    Matching: a support test (price came down, side +1) needs a demand zone,
    a resistance test (side -1) a supply zone. Near edge: the top of a demand
    zone or the bottom of a supply zone, where price reaches it first.
    """
    out = pd.DataFrame(False, index=events.index, columns=["in_zone", "zone_edge", "in_any_zone"])
    t_all = events["time"].to_numpy()
    p_all = events["price"].to_numpy()
    s_all = events["side"].to_numpy()
    z_lo, z_hi = zones["low"].to_numpy(), zones["high"].to_numpy()
    z_c, z_d = zones["created"].to_numpy(), zones["dies"].to_numpy()
    z_demand = (zones["kind"] == "demand").to_numpy()
    for _, idx in events.groupby("session").indices.items():
        t, p, s = t_all[idx], p_all[idx], s_all[idx]
        live_any = (z_c <= t.max()) & (z_d > t.min())
        if not live_any.any():
            continue
        zl, zh, zc, zd, zdem = z_lo[live_any], z_hi[live_any], z_c[live_any], z_d[live_any], z_demand[live_any]
        live = (zc[None, :] <= t[:, None]) & (zd[None, :] > t[:, None])
        inside = live & (zl[None, :] <= p[:, None]) & (p[:, None] <= zh[None, :])
        match = np.where(s[:, None] == 1, zdem[None, :], ~zdem[None, :])
        edge = np.where(zdem[None, :], p[:, None] == zh[None, :], p[:, None] == zl[None, :])
        out.iloc[idx, 0] = (inside & match).any(axis=1)
        out.iloc[idx, 1] = (inside & match & edge).any(axis=1)
        out.iloc[idx, 2] = inside.any(axis=1)
    return out
