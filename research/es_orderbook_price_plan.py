"""Price the ES order book for the journal (free quotes only, nothing is downloaded).

1. Every journal day, the windows around the owner's trades:
   - full book (MBO) from 00:00 UTC (the daily snapshot) to 15 minutes after the last exit
   - top 10 price levels each side (MBP-10) from 30 minutes before the first entry
2. Candidate weeks for the bot-vs-owner comparison, whole regular sessions
   (09:30-16:00 ET), every weekday:
   - full book (MBO) from 00:00 UTC to 16:00 ET
   - top 10 levels (MBP-10) 09:30-16:00 ET
   The contract is that session's front ES contract (most volume in the ES 1-minute bars).

Writes research/results/journal/es_orderbook_price_plan.csv and es_week_price_plan.csv.
Run from the repo root:   python -m research.es_orderbook_price_plan
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import requests

from research.databento_http import BASE_URL, DATASET
from research.journal_study import ES_BARS, ES_SYMBOLOGY, OUT

WEEKS = ["2024-06-03", "2024-07-29", "2024-04-08", "2024-09-23", "2024-02-26"]   # Mondays


def quote(symbol: str, schema: str, start: str, end: str) -> float:
    """Free price quote, with a short timeout and retries (some requests hang)."""
    params = dict(dataset=DATASET, symbols=symbol, schema=schema, start=start, end=end, stype_in="raw_symbol")
    for attempt in range(5):
        try:
            r = requests.get(f"{BASE_URL}/metadata.get_cost", params=params, timeout=20)
            r.raise_for_status()
            return float(r.text)
        except Exception as err:                       # network hiccup: try again
            print("retry", symbol, schema, start, type(err).__name__, flush=True)
            time.sleep(2)
    raise RuntimeError(f"no quote for {symbol} {start}")


def quote_all(jobs: list[tuple]) -> list[float]:
    with ThreadPoolExecutor(max_workers=6) as pool:
        return list(pool.map(lambda j: quote(*j), jobs))


def utc(day: str, hhmm: str) -> str:
    return pd.Timestamp(f"{day} {hhmm}", tz="America/New_York").tz_convert("UTC").strftime("%Y-%m-%dT%H:%M")


def front_contracts() -> pd.Series:
    """Front ES contract per session date (most volume that day)."""
    from research.market_data import load_bars
    b = load_bars(ES_BARS, ES_SYMBOLOGY)
    v = b.groupby(["session", "contract"])["volume"].sum().reset_index()
    v = v.sort_values("volume").groupby("session").tail(1)
    return v.set_index(v["session"].dt.date.astype(str))["contract"]


def main() -> None:
    p = pd.read_csv(OUT / "replay_price_plan.csv")
    rows = []
    for r in p.itertuples():
        es = r.mes.replace("MES", "ES")
        look = (pd.Timestamp(r.start) - pd.Timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M")
        rows.append(dict(day=r.day, es=es, trades=r.trades, start=r.start, end=r.end,
                         mbo_start=f"{r.day}T00:00", mbp10_start=look))
    q = pd.DataFrame(rows)
    q["es_mbo"] = quote_all([(x.es, "mbo", x.mbo_start, x.end) for x in q.itertuples()])
    print("journal days: full book priced", flush=True)
    q["es_mbp10"] = quote_all([(x.es, "mbp-10", x.mbp10_start, x.end) for x in q.itertuples()])
    print("journal days: top 10 levels priced", flush=True)
    q.to_csv(OUT / "es_orderbook_price_plan.csv", index=False)

    front = front_contracts()
    wrows = []
    for monday in WEEKS:
        for k in range(5):
            day = (pd.Timestamp(monday) + pd.Timedelta(days=k)).date().isoformat()
            if day not in front.index:
                continue                               # market holiday
            es = front[day]
            wrows.append(dict(week=monday, day=day, es=es, mbo_start=f"{day}T00:00",
                              rth_start=utc(day, "09:30"), rth_end=utc(day, "16:00")))
    w = pd.DataFrame(wrows)
    w["es_mbo"] = quote_all([(x.es, "mbo", x.mbo_start, x.rth_end) for x in w.itertuples()])
    w["es_mbp10"] = quote_all([(x.es, "mbp-10", x.rth_start, x.rth_end) for x in w.itertuples()])
    w.to_csv(OUT / "es_week_price_plan.csv", index=False)

    busy = pd.read_csv(OUT / "orderbook_price_plan.csv").head(20)["day"]
    print(f"journal: {q['day'].nunique()} days, {q['trades'].sum()} trades")
    print(f"  ES full book, all days: ${q['es_mbo'].sum():.2f} "
          f"(busiest 20: ${q[q['day'].isin(busy)]['es_mbo'].sum():.2f}, other days: ${q[~q['day'].isin(busy)]['es_mbo'].sum():.2f})")
    print(f"  ES top 10 levels, all days: ${q['es_mbp10'].sum():.2f}")
    print("weeks (whole regular sessions):")
    print(w.groupby("week")[["es_mbo", "es_mbp10"]].sum().round(2).to_string())


if __name__ == "__main__":
    main()
