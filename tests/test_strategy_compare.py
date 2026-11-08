"""Tests for the 1-minute-bar exit used in the strategy comparison (research.strategy_compare.bar_bracket)."""

import numpy as np

from research.bot_week_study import SLIP_TICKS
from research.strategy_compare import bar_bracket


def bars(rows):
    lo, hi, cl = (np.array(c, dtype=np.int64) for c in zip(*rows))
    return lo, hi, cl


def test_stop_counts_first_when_a_bar_reaches_both():
    lo, hi, cl = bars([(4000, 4000, 4000), (3980, 4045, 4000)])        # bar 1 reaches -5 and +11.25
    pts, k = bar_bracket(lo, hi, cl, 0, 1, 4000, 1, 4.5, 10.0)
    assert pts == -18 - SLIP_TICKS and k == 1


def test_target_needs_one_tick_through():
    lo, hi, cl = bars([(4000, 4000, 4000), (3999, 4040, 4038), (4030, 4041, 4041)])
    pts, k = bar_bracket(lo, hi, cl, 0, 2, 4000, 1, 4.5, 10.0)
    assert pts == 40 and k == 2


def test_stop_moves_to_plus_one_after_plus_seven():
    lo, hi, cl = bars([(4000, 4000, 4000), (3999, 4028, 4026), (4003, 4010, 4004)])   # +7, then back to +0.75
    pts, k = bar_bracket(lo, hi, cl, 0, 2, 4000, 1, 4.5, 10.0)
    assert pts == 4 - SLIP_TICKS and k == 2


def test_hold_to_close_without_target():
    lo, hi, cl = bars([(4000, 4000, 4000), (3995, 4010, 4008)])
    pts, k = bar_bracket(lo, hi, cl, 0, 1, 4000, -1, 4.5, None, move=False)
    assert pts == -8 - SLIP_TICKS and k == 1
