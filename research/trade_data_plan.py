"""Price trade data for the strategy backtest (build plan step 2). Pricing is free.

Each session uses its own front contract (from the level study's session
table), so roll weeks don't pay for two contracts.

Run from the repo root:   python -m research.trade_data_plan
"""

from __future__ import annotations

import pandas as pd

from research.databento_http import get_cost
from research.level_study import OUT

NY = "America/New_York"


def session_window(session: pd.Timestamp, hours: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    """UTC start/end of a session: full Globex (18:00 the day before to 17:00) or regular hours."""
    day = pd.Timestamp(session.date())
    if hours == "full":
        start = (day - pd.Timedelta(days=1)).replace(hour=18)
        end = day.replace(hour=17)
    elif hours == "rth":
        start, end = day.replace(hour=9, minute=30), day.replace(hour=16)
    else:
        raise ValueError(hours)
    return start.tz_localize(NY).tz_convert("UTC"), end.tz_localize(NY).tz_convert("UTC")


def request_ranges(first: str, last: str, hours: str = "full") -> list[tuple[str, str, str]]:
    """(contract, start, end) requests covering the sessions first..last, one contract each.

    Full sessions of the same contract are merged into one request (weekends and
    the daily halt hold no data, so they cost nothing). Regular-hours requests are
    one per session.
    """
    s = pd.read_csv(OUT / "sessions.csv", parse_dates=["session"]).set_index("session")
    s = s[(s.index >= first) & (s.index <= last)]
    out = []
    for sess, row in s.iterrows():
        a, b = session_window(sess, hours)
        if hours == "full" and out and out[-1][0] == row["contract"]:
            out[-1] = (row["contract"], out[-1][1], b)
        else:
            out.append((row["contract"], a, b))
    fmt = "%Y-%m-%dT%H:%M"
    return [(c, a.strftime(fmt), b.strftime(fmt)) for c, a, b in out]


def price(first: str, last: str, hours: str = "full") -> float:
    return sum(get_cost(c, "trades", a, b, "raw_symbol") for c, a, b in request_ranges(first, last, hours))


def main() -> None:
    options = [
        ("3 months, full sessions", "2026-07-27", "2026-10-23", "full"),
        ("6 months, full sessions", "2026-04-27", "2026-10-23", "full"),
        ("6 months, regular hours only", "2026-04-27", "2026-10-23", "rth"),
        ("9 months, full sessions", "2026-01-26", "2026-10-23", "full"),
        ("12 months, regular hours only", "2025-10-27", "2026-10-23", "rth"),
    ]
    for name, first, last, hours in options:
        n = len(pd.read_csv(OUT / "sessions.csv", parse_dates=["session"]).query("@first <= session <= @last"))
        print(f"{name:32s} {first} to {last}  {n:3d} sessions  ${price(first, last, hours):7.2f}")


if __name__ == "__main__":
    main()
