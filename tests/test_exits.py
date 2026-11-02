"""Tests for the bot's exit (research.exits.bracket_exit): stop 4.5, target 10, stop to +1 after +7."""

import numpy as np
import pandas as pd

from research.bot_week_study import SLIP_TICKS
from research.exits import bracket_exit
from research.owner_rules import DayTrades


def path(points_from_fill):
    p = 4000 + np.round(np.array(points_from_fill) * 4).astype(np.int64)
    n = len(p)
    return DayTrades(trades=pd.DataFrame(dict(t=np.arange(1, n + 1, dtype=float), price=p, size=np.ones(n, int),
                                              side=["B"] * n)))


def run(points, move=True):
    dt = path(points)
    return bracket_exit(dt, 0.0, 4000, 1, 1e9, 4.5, 10.0, 7.0 if move else None, 1.0 if move else None)


def test_reaches_7_then_comes_back_exits_at_plus_1():
    ticks, _, how = run([2, 5, 7, 4, 1, -2, -5])
    assert how == "moved stop" and ticks == 4 - SLIP_TICKS


def test_same_trade_without_the_move_is_a_full_loss():
    ticks, _, how = run([2, 5, 7, 4, 1, -2, -5], move=False)
    assert how == "stop" and ticks == -18 - SLIP_TICKS


def test_target_after_the_move():
    ticks, _, how = run([2, 7, 5, 9, 10, 10.25])
    assert how == "target" and ticks == 40


def test_plain_stop_before_plus_7():
    ticks, _, how = run([1, 3, 0, -3, -4.5, -6])
    assert how == "stop" and ticks == -18 - SLIP_TICKS
