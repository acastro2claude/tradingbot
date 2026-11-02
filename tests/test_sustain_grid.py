"""Tests for the short sustained-volume stretch (research.sustain_grid.first_entry)."""

import numpy as np

from research.sustain_grid import first_entry


def arrays(ours, theirs, low, close, stop_at=None):
    low = np.array(low, float)
    return dict(ours=np.array(ours), theirs=np.array(theirs), low=low, close=np.array(close, float),
                run_min=np.fmin.accumulate(low), stop_at=len(low) if stop_at is None else stop_at)


def test_two_seconds_of_buyers_after_the_low():
    a = arrays(ours=[1, 1, 30, 40, 5], theirs=[20, 20, 5, 5, 5], low=[400, 398, 398, 399, 399], close=[399, 398, 399, 400, 400])
    assert first_entry(a, 2, 2.0, 1.0, 10.0) == 3          # seconds 2-3: 70 vs 10, no new low, price up


def test_not_while_price_makes_new_lows():
    a = arrays(ours=[1, 30, 40, 40], theirs=[20, 5, 5, 5], low=[400, 398, 396, 394], close=[399, 398, 396, 395])
    assert first_entry(a, 2, 2.0, 1.0, 10.0) is None        # buyers are aggressive but price keeps falling


def test_needs_a_real_surge():
    a = arrays(ours=[1, 1, 3, 4, 1], theirs=[20, 20, 1, 1, 1], low=[400, 398, 398, 399, 399], close=[399, 398, 399, 400, 400])
    assert first_entry(a, 2, 2.0, 1.0, 10.0) is None        # 7 contracts in 2 s is below 1x normal (20)
    assert first_entry(a, 2, 2.0, 0.3, 10.0) == 3


def test_nothing_after_the_zone_breaks():
    a = arrays(ours=[1, 1, 30, 40, 5], theirs=[20, 20, 5, 5, 5], low=[400, 398, 398, 399, 399], close=[399, 398, 399, 400, 400],
               stop_at=2)
    assert first_entry(a, 2, 2.0, 1.0, 10.0) is None
