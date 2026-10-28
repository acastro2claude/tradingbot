"""Order book rebuild from MBO records (synthetic events)."""

import numpy as np
import pandas as pd

from research.orderbook import ADD, ASK, BID, CLEAR, FILL, PULL, TICK_NANO, book_changes, build_book, check_book

DTYPE = [("ts_recv", "<u8"), ("order_id", "<u8"), ("price", "<i8"), ("size", "<u4"),
         ("action", "S1"), ("side", "S1")]
T0 = 1_700_000_000


def records(rows):
    """rows: (second, order id, price in points, size, action, side)."""
    return np.array([(int((T0 + s) * 1e9), o, int(p / 0.25) * TICK_NANO, q, a, sd) for s, o, p, q, a, sd in rows],
                    dtype=DTYPE)


def test_fill_then_modify_counts_as_filled_not_pulled():
    a = records([
        (0, 1, 100.00, 5, b"A", b"A"),        # 5 offered at 100
        (1, 9, 100.00, 2, b"T", b"B"),        # buyer lifts 2
        (1, 1, 100.00, 2, b"F", b"A"),
        (1, 1, 100.00, 3, b"M", b"A"),        # resting order now 3
        (2, 1, 100.00, 1, b"C", b"A"),        # 1 pulled
    ])
    ch = book_changes(a)
    assert list(ch["kind"]) == [ADD, FILL, PULL]
    assert list(ch["qty"]) == [5, -2, -1]
    assert ch["side"].eq(ASK).all()


def test_move_and_clear():
    a = records([
        (0, 1, 99.75, 4, b"A", b"B"),
        (1, 1, 99.50, 4, b"M", b"B"),         # moved down one tick
        (2, 2, 100.25, 7, b"A", b"A"),
        (3, 0, 0.0, 0, b"R", b"N"),
    ])
    ch = book_changes(a)
    assert list(zip(ch["kind"], ch["price"], ch["qty"])) == [
        (ADD, 399, 4), (PULL, 399, -4), (ADD, 398, 4), (ADD, 401, 7), (CLEAR, 398, -4), (CLEAR, 401, -7)]


def test_depth_grid_by_second():
    a = records([
        (0, 1, 99.75, 4, b"A", b"B"),
        (0, 2, 100.00, 6, b"A", b"A"),
        (2, 3, 100.00, 1, b"T", b"B"),        # trade inside the window sets the price range
        (2, 2, 100.00, 1, b"F", b"A"),
        (2, 2, 100.00, 5, b"M", b"A"),
        (3, 1, 99.75, 4, b"C", b"B"),
    ])
    book = build_book(a, T0 + 1, T0 + 5, pad_points=1)
    col_b, col_a = 399 - book.lo, 400 - book.lo
    assert list(book.bid[:, col_b]) == [4, 4, 0, 0]     # end of seconds 1, 2, 3, 4
    assert list(book.ask[:, col_a]) == [6, 5, 5, 5]
    assert book.best(0) == (399, 400)
    assert book.row(T0 + 3.5) == 1                      # just before 3.5 s: end of second 2
    f = book.flows.set_index(["sec", "side", "price"])
    assert f.loc[(1, ASK, 400), "filled"] == 1
    assert f.loc[(2, BID, 399), "pulled"] == 4
    assert check_book(book)["crossed_seconds"] == 0
    assert isinstance(book.trades, pd.DataFrame) and len(book.trades) == 1
