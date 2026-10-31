"""Tests for the coded entry read (research.entry_read.read_test) on made-up tapes. Long at a demand zone."""

import numpy as np
import pandas as pd

from research.absorption import find_bursts
from research.entry_read import read_test
from research.owner_rules import DayTrades

NEAR, FAR, T = 400, 392, 100.0          # zone 98.00-100.00 in ticks; the test at t = 100 s


def tape(rows):
    df = pd.DataFrame(rows, columns=["t", "price", "size", "side"]).sort_values("t", kind="stable")
    df["price"] = df["price"].astype(np.int64)
    b = find_bursts(df["t"].to_numpy(), df["price"].to_numpy(), df["size"].to_numpy(), df["side"].to_numpy(), 0.01, 100)
    b["direction"] = np.where(b["side"] == "B", -1, 1)
    return DayTrades(trades=df), b


def falling_into_zone():
    return [(float(k), 420 - k // 5, 2, "A") for k in range(0, 100)]       # sellers push price down to 400 by t = 100


def test_absorption_then_buyers_gives_a_long():
    rows = falling_into_zone()
    rows += [(100.0 + 0.001 * j, 399, 20, "A") for j in range(6)]          # 120 sold at 399 within 0.01 s: absorption
    rows += [(101.0 + k, 399, 1, "A") for k in range(5)]                    # sellers fail to push lower
    rows += [(110.0 + k, 399 + k // 3, 10, "B") for k in range(20)]         # buyers come in, price lifts to 405
    dt, b = tape(rows)
    got = read_test(dt, b, T, 1, NEAR, FAR)
    assert got is not None and got["signal"].startswith("A")
    assert got["entry_y"] >= 403


def test_no_trade_when_sellers_break_through_the_zone():
    rows = falling_into_zone()
    rows += [(100.0 + k, 400 - k, 5, "A") for k in range(20)]               # sellers keep going to 381: 2+ points past the far edge
    rows += [(130.0 + k, 381 + k, 10, "B") for k in range(10)]
    dt, b = tape(rows)
    assert read_test(dt, b, T, 1, NEAR, FAR) is None


def test_no_trade_while_sellers_stay_in_control():
    rows = falling_into_zone()
    for k in range(60):                                                     # price bounces but sellers outtrade buyers
        rows += [(100.0 + 2 * k, 400 + (k % 6), 1, "B"), (100.5 + 2 * k, 400 + (k % 6), 5, "A")]
    dt, b = tape(rows)
    assert read_test(dt, b, T, 1, NEAR, FAR) is None
