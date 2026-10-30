"""Score the owner's blind review: did the takes do better than the skips?

For every reviewed chart (batch 1: review/review_answers.csv + review_key.csv; batch 2:
review/batch2/answers.csv + review_key.csv), from the bot's fill (the trigger trade + 1 tick)
it measures what price did next, using the saved ES trades:
  - best and worst point in the next 30 minutes
  - 2-point stop / 4-point target (targets fill 1 tick through, stops pay 1 tick)
  - 3-point stop / 6-point target
  - the owner's own target where one was given (else 6 points), with a 3-point stop
Then compares takes with skips ("wait" counts as a skip), with a Fisher exact test on
the 2/4 win rates. Writes review/review_scored.csv and review/review_score_summary.csv.

Run from the repo root:   python -m research.review_score
"""

from __future__ import annotations

import math
import re

import numpy as np
import pandas as pd

from research.bot_week_study import CONTRACTS, ES_POINT, FLAT_ET, SLIP_TICKS, TICK, TRADES_DIR, et
from research.journal_study import OUT
from research.settings import load_settings

REVIEW = OUT / "review"
HORIZON_MIN = 30


def tk(points: float) -> int:
    return int(round(points / TICK))


def first_hits(m: np.ndarray, stop: int, tgt: int) -> tuple[float, float]:
    hs = np.nonzero(m <= -stop)[0]
    ht = np.nonzero(m >= tgt + 1)[0]
    return (hs[0] if len(hs) else np.inf), (ht[0] if len(ht) else np.inf)


def result(m: np.ndarray, stop: int, tgt: int) -> float:
    """Points per contract: target, stop (+1 tick slippage), or the last price if neither by the close."""
    a, b = first_hits(m, stop, tgt)
    if a == b == np.inf:
        return (m[-1] - SLIP_TICKS) * TICK if len(m) else 0.0
    return tgt * TICK if b < a else -(stop + SLIP_TICKS) * TICK


def load_batches() -> pd.DataFrame:
    b1 = pd.read_csv(REVIEW / "review_answers.csv")
    b1 = b1.assign(batch="batch1", owner_target=b1.get("your target"))[["batch", "review", "owner", "owner_target",
                                                                        "owner_reason"]]
    b1 = b1.merge(pd.read_csv(REVIEW / "review_key.csv"), on="review")
    b2 = pd.read_csv(REVIEW / "batch2" / "answers.csv").assign(batch="batch2")
    b2 = b2[["batch", "review", "owner", "owner_target", "owner_reason"]].merge(
        pd.read_csv(REVIEW / "batch2" / "review_key.csv"), on="review")
    return pd.concat([b1, b2], ignore_index=True)


def fisher_two_sided(a: int, b: int, c: int, d: int) -> float:
    """Fisher exact test p-value for the 2x2 table [[a, b], [c, d]]."""
    n1, n2, k = a + b, c + d, a + c

    def p(x):
        return math.comb(n1, x) * math.comb(n2, k - x) / math.comb(n1 + n2, k)
    obs = p(a)
    return float(sum(p(x) for x in range(max(0, k - n2), min(k, n1) + 1) if p(x) <= obs * (1 + 1e-9)))


def main() -> None:
    s = load_settings()
    cost = CONTRACTS * s["backtest"]["commission_per_contract_round_trip"]
    tests = pd.read_csv(OUT / "level_tests_es_weeks.csv")
    plan = pd.read_csv(OUT / "es_week_price_plan.csv").set_index("day")
    x = load_batches().merge(tests[["day", "entry_time", "entry_price"]], on=["day", "entry_time"], how="left")
    x = x.drop_duplicates(["batch", "review"])      # two tests can trigger on the same trade (same entry)
    rows = []
    for day, g in x.groupby("day"):
        tr = pd.read_parquet(TRADES_DIR / f"{plan.loc[day, 'es']}_{day}.parquet").sort_values("t", kind="stable")
        t, px = tr["t"].to_numpy(), tr["price"].to_numpy()
        flat = et(day, FLAT_ET)
        for r in g.itertuples():
            d = int(r.direction)
            fill = int(r.entry_price) + d * SLIP_TICKS
            i, j = np.searchsorted(t, [r.entry_time, flat], "right")
            m = (px[i:j] - fill) * d
            k = int(np.searchsorted(t[i:j], r.entry_time + HORIZON_MIN * 60, "right"))
            tgt = np.nan
            if isinstance(r.owner_target, str) and re.search(r"\d", r.owner_target):
                tgt = float(re.findall(r"\d+(?:\.\d+)?", r.owner_target)[0])
            elif isinstance(r.owner_target, (int, float)) and not pd.isna(r.owner_target):
                tgt = float(r.owner_target)
            own = (tgt / TICK - fill) * d if not np.isnan(tgt) else np.nan
            own_ticks = int(round(own)) if not np.isnan(own) and own >= tk(1) else tk(6)
            rows.append(dict(batch=r.batch, review=r.review, owner=r.owner, side="long" if d == 1 else "short",
                             sources=r.sources, best_30m=m[:k].max() * TICK if k else 0.0,
                             worst_30m=-m[:k].min() * TICK if k else 0.0,
                             pts_2_4=result(m, tk(2), tk(4)), pts_3_6=result(m, tk(3), tk(6)),
                             owner_target_pts=own_ticks * TICK, pts_owner_target=result(m, tk(3), own_ticks)))
    sc = pd.DataFrame(rows).sort_values(["batch", "review"])
    sc["took"] = sc["owner"] == "take"
    sc.to_csv(REVIEW / "review_scored.csv", index=False)

    out = []
    for scope, g in (("batch 2 (40 charts)", sc[sc["batch"] == "batch2"]), ("both batches (52 charts)", sc)):
        for took, h in g.groupby("took"):
            row = dict(scope=scope, group="your takes" if took else "your skips", charts=len(h))
            for col, label in (("pts_2_4", "2/4"), ("pts_3_6", "3/6"), ("pts_owner_target", "3 stop, your target")):
                row[f"win {label}"] = (h[col] > 0).mean()
                row[f"$/trade {label}"] = h[col].mean() * ES_POINT - cost
            row["median best 30m"] = h["best_30m"].median()
            row["median worst 30m"] = h["worst_30m"].median()
            out.append(row)
        t_, s_ = g[g["took"]], g[~g["took"]]
        a, b = int((t_["pts_2_4"] > 0).sum()), int((t_["pts_2_4"] <= 0).sum())
        c, d = int((s_["pts_2_4"] > 0).sum()), int((s_["pts_2_4"] <= 0).sum())
        out.append(dict(scope=scope, group="takes vs skips, 2/4 wins: Fisher p", charts=len(g),
                        **{"win 2/4": fisher_two_sided(a, b, c, d)}))
    summary = pd.DataFrame(out)
    summary.to_csv(REVIEW / "review_score_summary.csv", index=False)
    pd.set_option("display.width", 250)
    print(summary.round(3).to_string())


if __name__ == "__main__":
    main()
