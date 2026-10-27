import numpy as np
import pandas as pd
import pytest

from research.crossings import band_relative_volume, band_visits
from research.levels import cluster_pocs
from research.market_data import build_sessions, session_dates
from research.paths import bar_path, trade_path
from research.profiles import bar_volume_at_price, poc, trade_volume_at_price, value_area
from research.touches import outcome, touch_events


# ---------- sessions ----------

def _utc(*stamps):
    return pd.Series(pd.to_datetime(list(stamps))).dt.tz_localize("America/New_York").dt.tz_convert("UTC")


def test_session_dates_regular_and_sunday_open():
    s = session_dates(_utc("2025-10-21 17:59", "2025-10-21 18:00", "2025-10-26 18:00", "2025-10-27 09:30"))
    assert list(s.dt.strftime("%Y-%m-%d")) == ["2025-10-21", "2025-10-22", "2025-10-27", "2025-10-27"]


def test_session_dates_across_daylight_saving_changes():
    # Clocks went back on Sun 2 Nov 2025 and forward on Sun 8 Mar 2026.
    s = session_dates(_utc("2025-11-02 18:00", "2025-11-02 23:30", "2026-03-08 18:00", "2026-03-08 23:59"))
    assert list(s.dt.strftime("%Y-%m-%d")) == ["2025-11-03", "2025-11-03", "2026-03-09", "2026-03-09"]


def test_build_sessions_measures_roll_gap_and_shifts_old_contract():
    rows = []
    def add(day, contract, minute, price, vol):
        ts = pd.Timestamp(f"{day} 10:00", tz="America/New_York").tz_convert("UTC") + pd.Timedelta(minutes=minute)
        rows.append(dict(ts=ts, instrument_id=1, contract=contract, open=price, high=price,
                         low=price, close=price, volume=vol))
    for m in range(60):
        add("2025-06-12", "MESM5", m, 100, 50)          # old contract only
        add("2025-06-12", "MESU5", m, 140, 1)
        add("2025-06-13", "MESM5", m, 102, 10)          # roll day: new contract leads
        add("2025-06-13", "MESU5", m, 142 + (m % 2), 50)
    bars = pd.DataFrame(rows)
    bars["session"] = session_dates(bars["ts"])
    fb, sess = build_sessions(bars)
    assert list(sess["contract"]) == ["MESM5", "MESU5"]
    assert sess.loc["2025-06-13", "roll_gap_ticks"] in (40, 41)
    assert sess.loc["2025-06-13", "offset_ticks"] == 0
    gap = sess.loc["2025-06-13", "roll_gap_ticks"]
    assert sess.loc["2025-06-12", "offset_ticks"] == gap
    old = fb[fb["session"] == "2025-06-12"]
    assert set(old["contract"]) == {"MESM5"}
    assert (old["close_adj"] == 100 + gap).all()
    assert bool(sess["short"].all())   # these toy sessions end at 11:00 ET


# ---------- profiles ----------

def test_bar_volume_is_spread_evenly_over_the_range():
    ticks, vol = bar_volume_at_price(low=[10, 12], high=[13, 12], volume=[8, 5])
    assert list(ticks) == [10, 11, 12, 13]
    assert list(vol) == [2, 2, 7, 2]


def test_trade_profile_poc_and_value_area():
    ticks, vol = trade_volume_at_price([5, 5, 6, 7, 7, 7, 8, 9], [1, 1, 2, 3, 3, 4, 2, 1])
    assert poc(ticks, vol) == 7
    lo, hi = value_area(ticks, vol, 0.70)   # total 17, 70% = 11.9: 7 (10) + one neighbour (2)
    assert (lo, hi) == (7, 8)               # 6 and 8 tie at 2; ties extend upward


def test_poc_tie_goes_to_price_nearest_the_average():
    assert poc([1, 2, 3, 4, 5], [5, 1, 1, 1, 5]) in (1, 5)
    assert poc([1, 2, 3, 4], [5, 4, 1, 5]) == 1   # average 2.33: 1 is nearer than 4


# ---------- POC groups ----------

def test_cluster_pocs_densest_group_first():
    groups = cluster_pocs([100, 101, 103, 110, 111, 112, 130], width_ticks=4)
    summary = [(g.count, g.low, g.high) for g in groups]
    assert (3, 110, 112) in summary
    assert (3, 100, 103) in summary
    assert (1, 130, 130) in summary
    assert all(g.high - g.low <= 4 for g in groups)


def test_cluster_pocs_width_limits_the_group_span():
    groups = cluster_pocs([0, 3, 5, 8], width_ticks=4)
    # three windows hold 2 POCs each; the narrowest (3, 5) wins, leaving 0 and 8 alone
    assert sorted((g.count, g.low) for g in groups) == [(1, 0), (1, 8), (2, 3)]
    assert sum(g.count for g in cluster_pocs([0, 3, 5, 8], width_ticks=8)) == 4


# ---------- paths ----------

def test_bar_path_up_bar_visits_low_first():
    p, t = bar_path([10], [13], [9], [12], [0.0])
    assert list(p) == [10, 9, 10, 11, 12, 13, 12]
    assert t[1] == pytest.approx(20.0) and t[-1] == pytest.approx(60.0)


def test_bar_path_gap_between_bars_is_instant():
    p, t = bar_path([10, 14], [10, 14], [10, 14], [10, 14], [0.0, 300.0])
    assert list(p) == [10, 11, 12, 13, 14]
    assert list(t[1:]) == [300.0] * 4


def test_trade_path_fills_jumps_and_drops_repeats():
    p, t = trade_path([10, 10, 12, 11], [0, 1, 2, 3])
    assert list(p) == [10, 11, 12, 11]
    assert list(t) == [0, 2, 2, 3]


# ---------- tests of a price ----------

def _walk(*points):
    """Path through turning points, one tick per second."""
    p = [points[0]]
    for a, b in zip(points[:-1], points[1:]):
        step = 1 if b > a else -1
        p.extend(range(a + step, b + step, step))
    return np.array(p), np.arange(len(p), dtype=float)


def test_support_test_that_holds():
    p, t = _walk(130, 100, 130)            # falls 30 ticks to 100, rallies 30
    ev = touch_events(p, t, [100], arm=20, brk=12, horizon=1e9)
    assert len(ev) == 1
    e = ev.iloc[0]
    assert e.side == 1 and e.test_no == 1 and not e.broke and e.mfe == 30
    assert outcome([e.mfe], [e.broke], 24)[0] == "hold"


def test_resistance_test_that_breaks():
    p, t = _walk(70, 100, 104, 100, 130)   # rises to 100, stalls 4 ticks up, then breaks 12+ through
    ev = touch_events(p, t, [100], arm=20, brk=12, horizon=1e9)
    assert len(ev) == 1
    e = ev.iloc[0]
    assert e.side == -1 and e.broke and e.mfe == 0
    assert outcome([e.mfe], [e.broke], 24)[0] == "break"


def test_retest_needs_price_to_move_away_first():
    # touches 100, wiggles 5 ticks away (not armed again), then goes 25 away and comes back
    p, t = _walk(130, 100, 105, 100, 125, 100, 80)
    ev = touch_events(p, t, [100], arm=20, brk=12, horizon=1e9)
    assert list(ev["test_no"]) == [1, 2]
    assert list(ev["mfe"]) == [25, 0]      # first test went 25 up before breaking; retest broke at once
    assert list(ev["broke"]) == [True, True]


def test_horizon_stops_the_clock():
    p, t = _walk(130, 100, 110, 100, 99, 130)
    ev = touch_events(p, t, [100], arm=20, brk=12, horizon=5)
    e = ev.iloc[0]
    assert not e.broke and e.mfe == 5      # only 5 seconds watched: up 5 ticks


# ---------- zone crossings ----------

def test_band_visits_cross_and_turn_back():
    p, t = _walk(0, 20, 10, 30)            # zones of 8: [0-7] [8-15] [16-23] [24-31]
    v = band_visits(p, t, 8)
    rows = list(v[["band", "direction", "crossed"]].itertuples(index=False, name=None))
    assert rows[0] == (1, 1, True)         # 8..15 crossed upward
    assert (2, 1, False) in rows           # 16..23 entered from below, turned back
    assert v.iloc[0]["seconds"] == 8


def test_band_relative_volume():
    profile = np.array([1.0] * 8 + [0.0] * 8 + [1.0] * 8)  # 3 zones: 8, 0, 8
    bands, rel = band_relative_volume(profile, tick_lo=0, width=8, neighbourhood_bands=1)
    assert list(bands) == [0, 1, 2]
    assert rel[1] == pytest.approx(0.0)
    assert rel[0] == pytest.approx(8 / ((8 + 0) / 2))   # edge zone: only 2 zones to average


def test_stall_time_counts_until_price_leaves_the_area():
    p, t = _walk(130, 100, 105, 95, 105, 88)   # reaches 100 at 30s, chops +/-5, leaves 12 below
    ev = touch_events(p, t, [100], arm=20, brk=12, horizon=1e9, stall=12)
    e = ev.iloc[0]
    assert e.time == 30 and e.stall_after == 5 + 10 + 10 + 17
