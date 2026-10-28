"""Price MES trade data for replaying the owner's journal trades (pricing is free).

For each day in the journal (with corrected times), one request for the front
MES contract of the same month as the traded contract, covering 15 minutes
before the first entry to 15 minutes after the last exit. The absorption
markers come from MES, so MES trades are what the replay needs.

Run from the repo root:   python -m research.journal_replay_plan
"""

from __future__ import annotations

import pandas as pd

from research.databento_http import get_cost
from research.journal_study import OUT

PAD = pd.Timedelta(minutes=15)


def day_windows() -> pd.DataFrame:
    t = pd.read_csv(OUT / "journal_trades.csv")
    for c in ("entry_time", "exit_time"):                 # mixed summer/winter offsets: parse as UTC
        t[c] = pd.to_datetime(t[c], utc=True)
    t = t[t["closed"]]
    t["day"] = t["entry_time"].dt.tz_convert("America/New_York").dt.date
    t["mes"] = "MES" + t["contract"].str.extract(r"([HMUZ])\d(\d)$").agg("".join, axis=1)
    g = t.groupby(["day", "mes"]).agg(start=("entry_time", "min"), end=("exit_time", "max"),
                                     trades=("trade_id", "size")).reset_index()
    g["start"] = (g["start"] - PAD).dt.tz_convert("UTC").dt.strftime("%Y-%m-%dT%H:%M")
    g["end"] = (g["end"] + PAD).dt.tz_convert("UTC").dt.strftime("%Y-%m-%dT%H:%M")
    return g


def main() -> None:
    g = day_windows()
    g["cost"] = [get_cost(r.mes, "trades", r.start, r.end, "raw_symbol") for r in g.itertuples()]
    g.to_csv(OUT / "replay_price_plan.csv", index=False)
    per_trade = g["cost"].sum() / g["trades"].sum()
    print(f"{len(g)} day windows, {g['trades'].sum()} trades: total ${g['cost'].sum():.2f} "
          f"(about ${per_trade:.3f} a trade); median window cost ${g['cost'].median():.3f}")
    cheapest = g.sort_values("cost")
    for n in (20, 40, 60):
        print(f"  {n} days with the most trades: ${g.nlargest(n, 'trades')['cost'].sum():.2f} "
              f"for {g.nlargest(n, 'trades')['trades'].sum()} trades")
    print(f"  cheapest 40 days: ${cheapest.head(40)['cost'].sum():.2f} for {cheapest.head(40)['trades'].sum()} trades")


if __name__ == "__main__":
    main()
