"""Tests for scoring the owner's replay decisions (research.replay_score, research.replay_invalid)."""

import numpy as np
import pandas as pd

from research.bot_week_study import SLIP_TICKS
from research.owner_rules import DayTrades
from research.replay_invalid import label
from research.replay_score import fixed_exit


def day(prices):
    n = len(prices)
    return DayTrades(trades=pd.DataFrame(dict(t=np.arange(n, dtype=float), price=np.array(prices, dtype=np.int64),
                                              size=np.ones(n, dtype=int), side=np.array(["B"] * n))))


def test_fixed_exit_target_needs_one_tick_through():
    dt = day(list(range(100, 118)))                       # rises 1 tick at a time to 117
    assert fixed_exit(dt, 0.0, 100, 1, 1e9, 2, 4) == 16.0   # 4 points = 16 ticks, filled at 117
    dt = day(list(range(100, 117)))                       # stops exactly at the target: not filled
    assert fixed_exit(dt, 0.0, 100, 1, 1e9, 2, 4) == 16 - SLIP_TICKS


def test_fixed_exit_stop_pays_slippage():
    dt = day(list(range(100, 90, -1)))                    # falls to 91: the 2-point stop at 92 is hit
    assert fixed_exit(dt, 0.0, 100, 1, 1e9, 2, 4) == -8 - SLIP_TICKS


def test_fixed_exit_short_mirrors_long():
    dt = day(list(range(100, 82, -1)))
    assert fixed_exit(dt, 0.0, 100, -1, 1e9, 2, 4) == 16.0


def test_labels_from_notes():
    assert label("long", "") == "took the trade"
    assert label("skip", "Invalid zone") == "skipped: invalid zone"
    assert label("skip", "price already blew past this zone both up and down") == "skipped: invalid zone"
    assert label("skip", "news day") == "skipped: other reason"
    assert label("unsure", "zone is valid") == "not sure"
