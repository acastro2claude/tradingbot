"""The owner's own trades (TradesViz executions export in pastData/).

Rebuilds round-trip trades from the fills (grouped by TradesViz's trade id)
and summarises them in trading terms, to compare with the written rules and
the backtest.

Timestamps: the export marks times as US Pacific. The owner says many trades
had a timezone problem when imported. The March 2025 MES fills check out
against market data at their recorded times; 2024 trades are not checked yet,
so anything by time of day (by_hour.csv) is provisional.

Run from the repo root:   python -m research.journal_study
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent.parent
JOURNAL = sorted((REPO / "pastData").glob("*executions_export*.csv"))
OUT = REPO / "research" / "results" / "journal"
POINT_VALUE = {"ES": 50.0, "MES": 5.0}


def load_fills(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["ts"] = pd.to_datetime(df["date"], utc=True).dt.tz_convert("America/New_York")
    return df


def round_trips(fills: pd.DataFrame) -> pd.DataFrame:
    """One row per trade: side, size, average entry/exit, points per contract, $ after costs."""
    rows = []
    for sid, g in fills.sort_values("ts").groupby("spread_id"):
        g = g.sort_values("ts")
        first = g.iloc[0]
        d = 1 if first.side == "buy" else -1
        buys, sells = g[g.side == "buy"], g[g.side == "sell"]
        ins, outs = (buys, sells) if d == 1 else (sells, buys)
        size = ins.quantity.sum()
        avg_in = (ins.price * ins.quantity).sum() / size
        avg_out = (outs.price * outs.quantity).sum() / max(outs.quantity.sum(), 1)
        gross = ((sells.price * sells.quantity).sum() - (buys.price * buys.quantity).sum()) \
            * POINT_VALUE[first.underlying]
        costs = (g.commission + g.fees).sum()
        rows.append(dict(trade_id=sid, market=first.underlying, contract=first.symbol,
                         side="long" if d == 1 else "short", entry_time=first.ts, exit_time=g.ts.iloc[-1],
                         contracts=size, closed=buys.quantity.sum() == sells.quantity.sum(),
                         entry=avg_in, exit=avg_out, points=(avg_out - avg_in) * d,
                         gross_usd=gross, costs_usd=costs, net_usd=gross - costs, fills=len(g)))
    t = pd.DataFrame(rows).sort_values("entry_time").reset_index(drop=True)
    t["hold_minutes"] = (t.exit_time - t.entry_time).dt.total_seconds() / 60
    return t


def summary(t: pd.DataFrame) -> dict:
    c = t[t.closed]
    w, lo = c.points > 0, c.points < 0
    days = c.entry_time.dt.date
    daily = c.groupby(days)["net_usd"].sum()
    eq = c["net_usd"].cumsum().to_numpy()
    return {
        "trades": len(c),
        "trading days": days.nunique(),
        "trades per day (median)": float(c.groupby(days).size().median()),
        "win rate": w.mean(),
        "average win (points per contract)": c.points[w].mean(),
        "average loss (points per contract)": c.points[lo].mean(),
        "median loss (points)": c.points[lo].median(),
        "loss at 90th percentile (points)": c.points[lo].quantile(0.1),
        "median win (points)": c.points[w].median(),
        "median hold (minutes)": c.hold_minutes.median(),
        "net $ after costs": c.net_usd.sum(),
        "costs $": c.costs_usd.sum(),
        "profit factor (after costs)": c.net_usd[c.net_usd > 0].sum() / -c.net_usd[c.net_usd < 0].sum(),
        "winning days": (daily > 0).mean(),
        "worst day $": daily.min(),
        "max drawdown $": float((np.maximum.accumulate(np.concatenate([[0], eq]))[1:] - eq).max()),
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    t = round_trips(pd.concat([load_fills(p) for p in JOURNAL], ignore_index=True))
    t.to_csv(OUT / "journal_trades.csv", index=False)
    s = summary(t)
    for k, v in s.items():
        print(f"{k:38s} {v:,.2f}" if isinstance(v, float) else f"{k:38s} {v}")
    c = t[t.closed]
    hour = c.groupby(c.entry_time.dt.hour).agg(trades=("points", "size"), win_rate=("points", lambda x: (x > 0).mean()),
                                               avg_points=("points", "mean"), net_usd=("net_usd", "sum"))
    hour.index.name = "entry hour (ET)"
    hour.to_csv(OUT / "by_hour.csv")
    print(hour.round(2).to_string())
    pd.Series(s).to_csv(OUT / "summary.csv", header=["value"])


if __name__ == "__main__":
    main()
