"""Bookmap-style pictures of the bot's level tests, for the owner to check by eye.

Picks, from research/results/journal/level_tests_es_weeks.csv:
  - full-read trades that won and that lost
  - the test closest to each owner trade taken at a marked level
and draws each on the rebuilt ES book with the marked level and the owner's
trades nearby. Saves PNGs in research/results/journal/orderbook_charts/.

Run from the repo root:   python -m research.bot_week_charts
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from research.bot_week_study import BOOK_DIR, et
from research.journal_study import OUT
from research.level_test_study import owner_trades
from research.orderbook import build_book, load_mbo
from research.orderbook_chart import CHARTS, draw


def full_read(e: pd.DataFrame) -> pd.Series:
    return ((e["line_before"] >= 2) & (e["line_left_share"] >= 0.5) & (e["eaten"] > 0) & (e["stacking"] > 0)
            & (e["delta_10s"] > 0) & (e["absorption_max"] >= 50))


def main() -> None:
    x = pd.read_csv(OUT / "level_tests_es_weeks.csv")
    plan = pd.read_csv(OUT / "es_week_price_plan.csv").set_index("day")
    e = x[x["turn"] == "turned"]
    fr = e[full_read(e)]
    picks = pd.concat([fr[fr["points"] > 0].head(2).assign(why="full read, won"),
                       fr[fr["points"] < 0].head(2).assign(why="full read, lost"),
                       e[e["owner_points"].notna()].head(2).assign(why="you traded this test")])
    owner = owner_trades()
    CHARTS.mkdir(parents=True, exist_ok=True)
    for day, g in picks.groupby("day"):
        es = plan.loc[day, "es"]
        book = build_book(load_mbo(BOOK_DIR / f"{es}_{day}_mbo.dbn.zst"), int(et(day, "08:00")), int(et(day, "16:00")))
        mine = owner[owner["day"] == day]
        for r in g.itertuples():
            near = mine[(mine["t"] - r.entry_time).abs() <= 900]
            others = [(o.t, o.entry, o.direction, f"your {o.side} {o.entry:.2f} ({o.points:+.2f} pts)")
                      for o in near.itertuples()]
            when = pd.Timestamp(r.entry_time, unit="s", tz="UTC").tz_convert("America/New_York")
            side = "long" if r.direction == 1 else "short"
            level = (r.edge if r.lo == r.hi else (r.hi if r.direction == 1 else r.lo)) * 0.25
            path = CHARTS / f"bot_{day}_{when:%H%M%S}.png"
            draw(book, r.entry_time, int(r.direction), (r.entry_price + r.direction) * 0.25, r.exit_time,
                 (r.entry_price + r.direction) * 0.25 + r.points * r.direction, r.points,
                 f"{es} {day}: bot {side} at a {r.sources} test, {when:%H:%M:%S} ET ({r.why})", path,
                 who="bot", level=level, others=others)
            print("saved", path.name, r.why, f"{r.points:+.2f}")


if __name__ == "__main__":
    main()
