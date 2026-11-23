"""The owner's checklist inputs (pushes, CVD divergence, push volume) on hand-made tapes."""

import numpy as np
import pandas as pd

from research.learn_read import swing_features
from research.owner_rules import DayTrades

NO_BURSTS = pd.DataFrame(dict(time=[], price=[], contracts=[], direction=[], min_size=[]))


def tape(legs, t0=1000.0):
    """legs: (from_ticks, to_ticks, side, size per trade); one trade per tick step, one per second."""
    rows, t = [], t0
    for a, b, side, size in legs:
        step = 1 if b > a else -1
        for p in range(a, b + step, step):
            rows.append((t, p, size, side))
            t += 1.0
    return pd.DataFrame(rows, columns=["t", "price", "size", "side"])


def run(tr, d=1, near=4000, bursts=NO_BURSTS):
    dt = DayTrades(trades=tr)
    T = float(tr["t"].iat[0]) + 1.0
    pts = np.array([float(tr["t"].iat[-1]) + 1.0])
    idx = np.searchsorted(dt.t, pts, "right")
    return swing_features(dt, bursts, 1.0, T, d, near, pts, idx).iloc[-1]


def test_lower_low_on_weaker_selling_is_a_classic_divergence():
    tr = tape([(4040, 4004, "A", 4),     # first push down: heavy selling
               (4005, 4020, "B", 3),     # bounce 4 points
               (4019, 4000, "A", 1),     # second push: 1 point lower, much lighter selling
               (4001, 4012, "B", 3)])    # buyers come in
    f = run(tr)
    assert f["sw_pushes"] == 2
    assert f["sw_px_further"] == 1.0
    assert f["sw_px_cvd"] > 0
    assert f["sw_div_classic"] == 1.0 and f["sw_div_absorb"] == 0.0
    assert f["sw_p2_vol_ratio"] < 0.5
    assert f["sw_zone_pushes"] == 2 and f["sw_last_in_zone"] == 1.0
    assert f["sw_since_bounce"] == 3.0 and f["sw_since_balance"] == 1.0


def test_heavy_selling_that_gets_no_lower_is_absorption():
    tr = tape([(4040, 4004, "A", 1),
               (4005, 4020, "B", 1),
               (4019, 4006, "A", 30),    # far more selling, low 2 ticks above the first
               (4007, 4012, "B", 1)])
    f = run(tr)
    assert f["sw_px_further"] == -0.5
    assert f["sw_div_absorb"] == 1.0 and f["sw_div_classic"] == 0.0


def test_pushes_count_before_the_zone_and_for_shorts():
    # a short at a supply zone whose edge is 4040: buyers push up twice, the first stops 3 points under the zone
    tr = tape([(4000, 4028, "B", 2),
               (4027, 4018, "A", 2),
               (4019, 4040, "B", 2),
               (4039, 4030, "A", 2)])
    f = run(tr, d=-1, near=4040)
    assert f["sw_pushes"] == 2
    assert f["sw_zone_pushes"] == 1          # only the second came within 1 point of the zone
    assert f["sw_px_further"] == 3.0         # the second push went 3 points higher


def test_push_in_progress_and_no_push_yet():
    f = run(tape([(4040, 4060, "B", 1)]))    # price only rises: nothing has pushed against a long
    assert np.isnan(f["sw_pushes"])
    f = run(tape([(4040, 4004, "A", 1), (4005, 4012, "B", 1), (4011, 4004, "A", 1)]))
    assert f["sw_pushes"] == 2               # the second push (2 points down off the high) is still going
    assert f["sw_since_secs"] == 1.0
