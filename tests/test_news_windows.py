"""Tests for the news blocks in the backtest (research.backtest.news_windows)."""

import pandas as pd

from research.backtest import news_windows
from research.settings import load_settings

NY = "America/New_York"


def ts(s: str) -> float:
    return pd.Timestamp(s, tz=NY).timestamp()


def test_fed_day_blocks_the_whole_session(tmp_path):
    cal = tmp_path / "cal.csv"
    cal.write_text("date,time_et,event,blackout_until_et,source\n"
                   "2026-04-29,14:00,Fed decision,15:30,FOMC\n"
                   "2026-05-12,08:30,CPI,09:00,BLS\n")
    s = load_settings()
    w = news_windows(cal, s)
    (start, end, _), = w[pd.Timestamp("2026-04-29")]
    assert start == ts("2026-04-28 18:00") and end == ts("2026-04-29 17:00")
    (start, end, flat), = w[pd.Timestamp("2026-05-12")]
    b = s["bot"]
    assert start == ts("2026-05-12 08:30") - b["news_blackout_before_min"] * 60
    assert end == ts("2026-05-12 08:30") + b["news_blackout_after_min"] * 60
    assert flat == ts("2026-05-12 08:30") - b["close_before_news_min"] * 60
