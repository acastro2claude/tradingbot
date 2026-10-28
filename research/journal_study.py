"""The owner's own trades (TradesViz executions export in pastData/).

Rebuilds round-trip trades from the fills (grouped by TradesViz's trade id)
and summarises them in trading terms, to compare with the written rules and
the backtest.

Timestamps: the export marks times as US Pacific, but many trades were
imported with the wrong timezone (owner). Each trade is checked against ES
1-minute bars: its recorded time, and shifts of -3 to +3 hours. The shift
where every fill price lies inside that minute's range wins (MES fills get
1 tick of leeway against ES). Where several shifts fit, or none fits fully,
the trade takes the shift of that day's clear-cut trades (or the nearest
day's) and is flagged. The result is saved in results/journal/time_fix.csv,
so the bars are only needed to rebuild it.

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
ES_BARS = REPO / "data" / "raw" / "ES_FUT_ohlcv-1m_2024-01-16_2025-03-21.dbn.zst"
ES_SYMBOLOGY = REPO / "data" / "raw" / "ES_FUT_symbology.json"
SHIFTS = [-3, -2, -1, 0, 1, 2, 3]


def find_time_shifts(fills: pd.DataFrame) -> pd.DataFrame:
    """Per trade: the hour shift that puts every fill inside the market's 1-minute range."""
    from research.market_data import load_bars

    bars = load_bars(ES_BARS, ES_SYMBOLOGY)
    rng = {(c, m): (lo, hi) for c, m, lo, hi in
           zip(bars.contract, bars.ts.dt.floor("1min"), bars.low, bars.high)}
    f = fills.copy()
    f["utc"] = f["ts"].dt.tz_convert("UTC")
    f["es_contract"] = "ES" + f.symbol.str.extract(r"([HMUZ])\d(\d)$").agg("".join, axis=1)
    f["tick"] = np.rint(f.price / 0.25).astype(int)
    f["tol"] = np.where(f.underlying == "MES", 1, 0)
    rows = []
    for sid, g in f.groupby("spread_id"):
        score = {}
        for h in SHIFTS:
            ok = [(r := rng.get((c, (ts + pd.Timedelta(hours=h)).floor("1min")))) is not None
                  and r[0] - tol <= tk <= r[1] + tol
                  for c, ts, tk, tol in zip(g.es_contract, g.utc, g.tick, g.tol)]
            score[h] = float(np.mean(ok))
        best = max(score.values())
        fits = [h for h in SHIFTS if score[h] == best]
        rows.append(dict(trade_id=sid, day=g["ts"].min().date(), match=best,
                         clear=(best == 1.0 and len(fits) == 1), candidates=" ".join(map(str, fits)),
                         shift_hours=fits[0] if len(fits) == 1 else np.nan))
    t = pd.DataFrame(rows)
    clear = t[t.clear]
    by_day = clear.groupby("day")["shift_hours"].agg(lambda x: x.mode().iat[0])
    days = np.array(sorted(by_day.index))
    for i in t.index[~t.clear]:
        cands = [int(x) for x in t.at[i, "candidates"].split()]
        d = t.at[i, "day"]
        near = by_day.get(d)
        if near is None:                                   # nearest day with clear trades
            near = by_day.iloc[np.argmin([abs((x - d).days) for x in days])]
        t.at[i, "shift_hours"] = near if (near in cands or t.at[i, "match"] < 1) else cands[0]
    t["how"] = np.where(t.clear, "clear match", np.where(t.match < 1, "no full match: day's shift",
                                                         "several fit: day's shift"))
    return t


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


def apply_time_fix(fills: pd.DataFrame) -> pd.DataFrame:
    """Shift each fill by its trade's fix (recomputed if the ES bars are here, else read back)."""
    fix_file = OUT / "time_fix.csv"
    if ES_BARS.exists():
        fix = find_time_shifts(fills)
        fix.to_csv(fix_file, index=False)
    else:
        fix = pd.read_csv(fix_file)
    shift = fills["spread_id"].map(fix.set_index("trade_id")["shift_hours"]).fillna(0)
    out = fills.copy()
    out["ts"] = out["ts"] + pd.to_timedelta(shift, unit="h")
    return out


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    fills = apply_time_fix(pd.concat([load_fills(p) for p in JOURNAL], ignore_index=True))
    t = round_trips(fills)
    t.to_csv(OUT / "journal_trades.csv", index=False)
    s = summary(t)
    for k, v in s.items():
        print(f"{k:38s} {v:,.2f}" if isinstance(v, float) else f"{k:38s} {v}")
    c = t[t.closed]
    fix = pd.read_csv(OUT / "time_fix.csv")
    print("time fix:", fix["how"].value_counts().to_dict(), "| shifts:", fix["shift_hours"].value_counts().to_dict())
    hour = c.groupby(c.entry_time.dt.hour).agg(trades=("points", "size"), win_rate=("points", lambda x: (x > 0).mean()),
                                               avg_points=("points", "mean"), net_usd=("net_usd", "sum"))
    hour.index.name = "entry hour (ET)"
    hour.to_csv(OUT / "by_hour.csv")
    print(hour.round(2).to_string())
    pd.Series(s).to_csv(OUT / "summary.csv", header=["value"])


if __name__ == "__main__":
    main()
