"""Supply and demand zones, course rules (research.zones)."""

from pathlib import Path

import numpy as np
import pandas as pd

from research.settings import load_settings
from research.zones import find_zones, make_candles

FIXTURE = Path(__file__).parent / "fixtures" / "es_4h_2024-08-08_16.csv"   # ESU4 4-hour candles, contract ticks


def _settings(**kw) -> dict:
    return dict(load_settings()["zones"], **kw)


def _candles(rows) -> pd.DataFrame:
    c = pd.DataFrame(rows, columns=["open", "high", "low", "close"])
    c["start"] = np.arange(len(c)) * 3600.0
    c["end"] = c["start"] + 3600
    c["session"] = "day"
    return c


# 20 quiet candles, a rally of two strong candles, a one-candle red base, then the leg-out
QUIET = [(80, 84, 76, 80)] * 20
RALLY = [(80, 95, 79, 94), (94, 109, 93, 108)]
BASE = [(108, 111, 105, 107)]
LEG_OUT = [(107, 135, 106, 133)]


def _zone_at_leg_out(c: pd.DataFrame, z: pd.DataFrame) -> pd.DataFrame:
    return z[z["leg_out_start"] == c["start"].iat[len(c) - 1]]


def test_rally_base_rally_demand_zone():
    c = _candles(QUIET + RALLY + BASE + LEG_OUT)
    z = _zone_at_leg_out(c, find_zones(c, 60, _settings()))
    assert len(z) == 1
    r = z.iloc[0]
    assert (r.kind, r.pattern, r.base_candles) == ("demand", "RBR", 1)
    assert (r.near, r.far) == (108, 105)           # top of the base body, bottom of the wicks


def test_drop_base_drop_supply_zone_is_the_mirror():
    flip = [(200 - o, 200 - lo, 200 - h, 200 - c) for o, h, lo, c in QUIET + RALLY + BASE + LEG_OUT]
    c = _candles(flip)
    z = _zone_at_leg_out(c, find_zones(c, 60, _settings()))
    r = z.iloc[0]
    assert (r.kind, r.pattern) == ("supply", "DBD")
    assert (r.near, r.far) == (92, 95)             # bottom of the base body, top of the base wick


def test_no_zone_when_the_move_away_is_weak_or_stays_in_the_base():
    weak = _candles(QUIET + RALLY + BASE + [(107, 112, 106, 110)])     # closes inside the base's wick
    assert _zone_at_leg_out(weak, find_zones(weak, 60, _settings())).empty
    small = _candles(QUIET + RALLY + [(108, 111, 100, 104)] + [(104, 125, 103, 112)])  # base body 4, move-away body 8
    assert not _zone_at_leg_out(small, find_zones(small, 60, _settings(leg_out_body_ratio=2.0))).empty
    assert _zone_at_leg_out(small, find_zones(small, 60, _settings(leg_out_body_ratio=3.0))).empty


def test_owner_example_4h_demand_zone_aug_2024():
    """Owner's example (Oct 2026): a new 4-hour demand zone, base ending with the red candle
    of Wed 14 Aug 2024 22:00 PT, then the rally of Thu 15 Aug. On the owner's thinkorswim chart
    (back-adjusted, +60.75) the zone top is 5550.75, the open of that red candle."""
    c = pd.read_csv(FIXTURE)
    z = find_zones(c, 240, _settings())
    own = z[(z["kind"] == "demand") & (z["created_session"] == "2024-08-15")]
    assert len(own) == 1
    r = own.iloc[0]
    assert r.pattern == "RBR"
    assert r.near * 0.25 == 5490.00 and r.near * 0.25 + 60.75 == 5550.75
    assert r.far * 0.25 == 5471.75                 # the rally candle's low wick
    assert c.loc[c["start"] == r.base_start, "label"].item() == "2024-08-14 16:00"   # Chicago = 14:00 PT
    assert c.loc[c["start"] == r.leg_out_start, "label"].item() == "2024-08-15 04:00"


def test_candles_start_at_chicago_midnight():
    ts = pd.date_range("2024-08-15 08:00", "2024-08-15 09:59", freq="1min", tz="UTC")   # 03:00-04:59 CT
    bars = pd.DataFrame(dict(ts=ts, session=pd.Timestamp("2024-08-15"), open_adj=100, high_adj=101, low_adj=99,
                             close_adj=100, volume=1))
    c = make_candles(bars, 240, "America/Chicago")
    assert list(c["label"].dt.strftime("%H:%M")) == ["00:00", "04:00"]
    assert c["start"].iat[1] == pd.Timestamp("2024-08-15 09:00", tz="UTC").timestamp()
    assert c["start"].iat[0] == pd.Timestamp("2024-08-15 05:00", tz="UTC").timestamp()


def _zone(tf, low, high, created, dies):
    return dict(timeframe=tf, low=low, high=high, created=created, dies=dies, why="broken",
                tests=[created + 1.0, dies - 1.0])


def test_overlapping_zones_keep_the_higher_timeframe():
    from research.zones import resolve_overlaps
    z = pd.DataFrame([
        _zone(240, 10, 20, 100, 500),     # 4h zone, counts from 100 to 500
        _zone(60, 15, 18, 200, 450),      # forms inside the live 4h zone: not marked
        _zone(60, 12, 14, 50, 400),       # live 1h zone the 4h zone forms over at 100: ends there
        _zone(60, 16, 19, 600, 700),      # forms after the 4h zone ended: kept
        _zone(60, 30, 35, 10, 300),       # elsewhere: kept
        _zone(60, 33, 40, 50, 310),       # same timeframe, over the earlier live zone: not marked
    ])
    out = resolve_overlaps(z).set_index(["timeframe", "low"])
    assert sorted(out.index) == [(60, 12), (60, 16), (60, 30), (240, 10)]
    assert out.loc[(60, 12), "dies"] == 100
    assert out.loc[(60, 12), "why"] == "replaced by a higher timeframe"
    assert out.loc[(60, 12), "n_tests"] == 1          # its test at 399 came after it was replaced
    assert out.loc[(60, 16), "why"] == "broken"


def test_demand_base_needs_a_red_candle():
    green_base = _candles(QUIET + RALLY + [(107, 111, 105, 108)] + LEG_OUT)    # small green candle, then the rally
    assert _zone_at_leg_out(green_base, find_zones(green_base, 60, _settings())).empty
    assert not _zone_at_leg_out(green_base, find_zones(green_base, 60, _settings(base_needs_opposite_candle=False))).empty


def _bars(prices, step=60):
    ts = pd.to_datetime(np.arange(len(prices)) * step, unit="s", utc=True)
    p = np.asarray(prices)
    return pd.DataFrame(dict(ts=ts, low_adj=p, high_adj=p, close_adj=p))


def test_zone_life_counts_retests_and_skips_chop():
    from research.zones import zone_life
    zone = pd.DataFrame([dict(kind="demand", near=100, far=90, low=90, high=100, leg_out_start=0.0, created=60.0,
                              timeframe=15)])
    # leg-out bar, away 1.5x the 10-tick height (115+), back to 100 (test 1), away 20 ticks (5 points) again,
    # back (test 2), a bounce of only 1 tick (no new test), away, back (test 3), then a 15m close below 90
    bars = _bars([130, 120, 116, 105, 100, 110, 121, 115, 99, 101, 100, 125, 100, 95, 85])
    candles = {15: pd.DataFrame(dict(start=[780.0], end=[840.0], close=[85]))}
    life = zone_life(zone, bars, candles, _settings(), 0.25).iloc[0]
    assert life.tests == [240.0, 480.0, 720.0]
    assert (life.created, life.dies, life.why) == (60.0, 840.0, "broken")
    chop = _bars([112, 110, 104, 100, 108, 112])           # never gets 15 ticks away before coming back
    assert zone_life(zone, chop, candles, _settings(), 0.25).empty
