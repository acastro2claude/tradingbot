"""The bot's exit, tick by tick: stop-market, take profit, and the stop moved up after +7 points.

Owner (Oct-Nov 2026): stop-market 4.5 points from the entry, take profit at 10 points; once price
is 7 points past the entry the trade's way, the stop moves to entry + 1 point (at least breakeven).
Settings: [bot] stop_points, target_points, breakeven_trigger_points, breakeven_stop_points.
Stops (touched) and the flat close pay the slippage; the target fills when price trades 1 tick
through it; the stop move happens when price touches +7.
"""

from __future__ import annotations

import numpy as np

from research.bot_week_study import SLIP_TICKS
from research.owner_rules import DayTrades
from research.settings import load_settings

S = load_settings()
TICK = S["market"]["tick_size"]


def bracket_exit(dt: DayTrades, t_in: float, fill: int, d: int, flat: float, stop_pts: float, target_pts: float,
                 move_at_pts: float | None = None, move_to_pts: float | None = None,
                 slip: int = SLIP_TICKS) -> tuple[float, float, str]:
    """Ticks per contract, exit time and how it ended ("stop", "moved stop", "target", "close").
    slip: ticks paid on stops and the close (0 = before costs)."""
    i, j = int(np.searchsorted(dt.t, t_in, "right")), int(np.searchsorted(dt.t, flat, "right"))
    px, tt = dt.px[i:j], dt.t[i:j]
    if not len(px):
        return 0.0, t_in, "close"
    m = (px - fill) * d                                          # ticks our way at each trade
    s0, tg = -round(stop_pts / TICK), round(target_pts / TICK)

    def first(mask, start=0):
        k = np.flatnonzero(mask[start:])
        return start + int(k[0]) if len(k) else len(mask)

    ks, kt = first(m <= s0), first(m >= tg + 1)
    km = first(m >= round(move_at_pts / TICK)) if move_at_pts is not None else len(m)
    if km < min(ks, kt):                                         # +7 first: the stop moves up
        s1 = round(move_to_pts / TICK)
        ks2 = first(m <= s1, km + 1)
        if ks2 < kt:
            return float(s1 - slip), float(tt[ks2]), "moved stop"
        ks = len(m)
    if ks == kt == len(m):
        return float(m[-1] - slip), float(tt[-1]), "close"
    if ks <= kt:
        return float(s0 - slip), float(tt[ks]), "stop"
    return float(tg), float(tt[kt]), "target"


def bot_exit(dt: DayTrades, t_in: float, fill: int, d: int, flat: float, move: bool = True,
             move_to_pts: float | None = None) -> tuple[float, float, str]:
    """The owner's exit from settings; move=False is the plain 4.5/10 bracket."""
    b = S["bot"]
    return bracket_exit(dt, t_in, fill, d, flat, b["stop_points"], b["target_points"],
                        b["breakeven_trigger_points"] if move else None,
                        (b["breakeven_stop_points"] if move_to_pts is None else move_to_pts) if move else None)
