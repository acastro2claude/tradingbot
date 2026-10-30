"""Why the exits lose: do stopped trades come back and reach the target later the same day?

For every ES level test that turned (level_tests_es_weeks.csv), from the bot's fill
(entry + slippage) to the flatten time, using the saved trades (data/derived/es_weeks_trades):
  - for several stop/target pairs: won, stopped but the target traded later that day,
    or neither by the close
  - for tests that reached +4 points at all: how far price went against the trade first
  - for 2-point stops: how far price went our way before the stop
  - the same stop/target pairs on random entries (random time 09:30-15:30 and
    direction, filled at the next trade + slippage) on the same days, for fairness:
    over a whole day ES moves a lot, so "the target traded later" happens often anyway
Targets need price to trade 1 tick through them, as in the exit study.
Writes research/results/journal/exit_stop_check.csv and exit_stop_check_summary.csv.

Run from the repo root:   python -m research.exit_stop_check
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from research.bot_week_study import CONTRACTS, ES_POINT, FLAT_ET, SLIP_TICKS, TICK, TRADES_DIR, et
from research.journal_study import OUT
from research.settings import load_settings

PAIRS = [(2, 4), (3, 7), (3, 4), (4, 4), (5, 4)]     # (stop, target) in points
REF_TARGET, REF_STOP = 4, 2
RANDOM_PER_DAY = 100


def first_hits(m: np.ndarray, stop: int, tgt: int) -> tuple[float, float]:
    """Positions of the first stop and first target fill (np.inf if never) along ticks-in-favour m."""
    hs = np.nonzero(m <= -ticks(stop))[0]
    ht = np.nonzero(m >= ticks(tgt) + 1)[0]
    return (hs[0] if len(hs) else np.inf), (ht[0] if len(ht) else np.inf)


def random_baseline(days: list, plan: pd.DataFrame, seed: int) -> dict:
    rng = np.random.default_rng(seed)
    won = {f"{s}/{t}": [] for s, t in PAIRS}
    for day in days:
        tr = pd.read_parquet(TRADES_DIR / f"{plan.loc[day, 'es']}_{day}.parquet").sort_values("t", kind="stable")
        t, px = tr["t"].to_numpy(), tr["price"].to_numpy()
        flat = et(day, FLAT_ET)
        for t0 in rng.uniform(et(day, "09:30"), et(day, "15:30"), RANDOM_PER_DAY):
            d = int(rng.choice([-1, 1]))
            i, j = np.searchsorted(t, [t0, flat], "right")
            m = (px[i:j] - (px[i] + d * SLIP_TICKS)) * d
            for stop, tgt in PAIRS:
                a, b = first_hits(m, stop, tgt)
                won[f"{stop}/{tgt}"].append(b < a)
    return {k: float(np.mean(v)) for k, v in won.items()}


def ticks(points: float) -> int:
    return int(round(points / TICK))


def main() -> None:
    tests = pd.read_csv(OUT / "level_tests_es_weeks.csv")
    plan = pd.read_csv(OUT / "es_week_price_plan.csv").set_index("day")
    e = tests[tests["turn"] == "turned"]
    rows = []
    for day, g in e.groupby("day"):
        tr = pd.read_parquet(TRADES_DIR / f"{plan.loc[day, 'es']}_{day}.parquet").sort_values("t", kind="stable")
        t, px = tr["t"].to_numpy(), tr["price"].to_numpy()
        flat = et(day, FLAT_ET)
        for r in g.itertuples():
            d = int(r.direction)
            fill = int(r.entry_price) + d * SLIP_TICKS
            i, j = np.searchsorted(t, [r.entry_time, flat], "right")
            m = (px[i:j] - fill) * d                      # ticks in our favour, trade by trade
            tt = t[i:j]
            row = dict(test=r.Index, day=day)
            for stop, tgt in PAIRS:
                a, b = first_hits(m, stop, tgt)
                key = f"{stop}/{tgt}"
                row[f"won {key}"] = b < a
                row[f"stopped, target later {key}"] = a < b < np.inf
                row[f"neither {key}"] = a == b == np.inf
            ht = np.nonzero(m >= ticks(REF_TARGET) + 1)[0]
            row["reached +4 today"] = len(ht) > 0
            if len(ht):
                row["worst against before +4"] = -m[:ht[0] + 1].min() * TICK
                row["minutes to +4"] = (tt[ht[0]] - r.entry_time) / 60
            hs = np.nonzero(m <= -ticks(REF_STOP))[0]
            if len(hs):
                row["best our way before -2"] = m[:hs[0] + 1].max() * TICK
                row["minutes to -2"] = (tt[hs[0]] - r.entry_time) / 60
            rows.append(row)
    x = pd.DataFrame(rows)
    x.to_csv(OUT / "exit_stop_check.csv", index=False)

    rand = random_baseline(sorted(x["day"].unique()), plan, load_settings()["level_study"]["random_seed"])
    cost_pts = CONTRACTS * load_settings()["backtest"]["commission_per_contract_round_trip"] / ES_POINT
    summary = []
    for stop, tgt in PAIRS:
        key = f"{stop}/{tgt}"
        loss = stop + SLIP_TICKS * TICK
        summary.append(dict(stop_points=stop, target_points=tgt, level_tests_won=x[f"won {key}"].mean(),
                            stopped_then_target_later=x[f"stopped, target later {key}"].mean(),
                            neither_by_close=x[f"neither {key}"].mean(), random_entries_won=rand[key],
                            break_even=(loss + cost_pts) / (tgt + loss)))
    summary = pd.DataFrame(summary)
    summary.to_csv(OUT / "exit_stop_check_summary.csv", index=False)
    print(f"{len(x)} tests")
    print((summary.set_index(["stop_points", "target_points"]) * 100).round(0).to_string())
    w = x[x["reached +4 today"]]
    print(f"reached +4 at some point before the close: {x['reached +4 today'].mean():.0%}")
    print("  worst point against first (pts):", w["worst against before +4"].quantile([.25, .5, .75, .9]).round(2).to_dict())
    print(f"  dipped 2+ pts first: {(w['worst against before +4'] >= 2).mean():.0%} | 3+: "
          f"{(w['worst against before +4'] >= 3).mean():.0%} | 5+: {(w['worst against before +4'] >= 5).mean():.0%}")
    print(f"  minutes to +4 (median): {w['minutes to +4'].median():.1f}")
    s = x[x["minutes to -2"].notna()]
    print(f"2-pt stops: minutes to the stop (median) {s['minutes to -2'].median():.1f} | went 1+ pt our way first "
          f"{(s['best our way before -2'] >= 1).mean():.0%} | 2+ pts {(s['best our way before -2'] >= 2).mean():.0%}")


if __name__ == "__main__":
    main()
