"""Tests for the Topstep account replay (research.topstep_risk)."""

import pandas as pd

from research.topstep_risk import account, day_filter, floor_of


def day(usd, times=None):
    times = times or [float(i * 100) for i in range(len(usd))]
    return pd.DataFrame(dict(entry_time=times, exit_time=[t + 50 for t in times], usd=usd, day="2024-05-01"))


def test_day_rules():
    g = day([-300.0, -300.0, 500.0])
    assert day_filter(g, "none", 1000) == [True, True, True]
    assert day_filter(g, "2 trades", 1000) == [True, True, False]
    assert day_filter(g, "2 losses", 1000) == [True, True, False]
    assert day_filter(g, "-500 on the day", 1000) == [True, True, False]
    assert day_filter(g, "-1000 on the day", 1000) == [True, True, True]
    assert day_filter(g, "none", 600) == [True, True, False]          # the account's daily loss limit


def test_one_trade_at_a_time():
    g = day([100.0, 100.0], times=[0.0, 20.0])                        # the second starts while the first is open
    assert day_filter(g, "none", 1000) == [True, False]


def test_trailing_floor():
    assert floor_of(53000, 50000, 2000, lock=False) == 51000
    assert floor_of(53000, 50000, 2000, lock=True) == 50000
    t = pd.DataFrame(dict(day=["d1", "d2", "d3"], usd=[2000.0, -1900.0, -200.0]))
    # best close 52,000 -> floor 50,000; 50,100 survives; 49,900 is below the floor
    assert account(t, 50000, 2000, lock=False)[0] == 1
    assert account(t.iloc[:2], 50000, 2000, lock=False)[0] == 0
