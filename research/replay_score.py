"""Score the owner's zone replay decisions (research.replay_page) against the bot and random entries.

For each clip the owner decided: their entry (the last price when they pressed, + 1 tick, the
zone's side or against it) traded with the owner's risk structure ([run_winners]: 4-point stop,
10-point target, and 10-or-12 with the delta check) plus 2/4 and 3/7; the bot's entry on the
same clip (first 1-point turn off the test, research.level_test_book.TestReader.turn_trade);
and random entries (random second from the test to 5 minutes after it, random side). Costs:
$12.40 per trade (10 MES = 1 ES at $50 a point).

Reads the decisions saved from the page (ArtifactData list -> data/derived/replay/decisions/). Results
are split by sitting: the first 104 decisions (the rules were read from these), the second look
at 31 clips on the fixed chart, and the new clips of the second sitting (a fresh check).
Writes research/results/journal/replay_scores.csv and replay_score_summary.csv.
Run from the repo root:   python -m research.replay_score
"""

from __future__ import annotations

import glob
import json

import numpy as np
import pandas as pd

from research.bot_week_study import CONTRACTS, ES_POINT, FLAT_ET, SLIP_TICKS, et
from research.journal_study import OUT
from research.level_study import DERIVED
from research.level_test_book import TestReader
from research.owner_rules import DayTrades
from research.run_winners import exit_trade
from research.settings import load_settings

DECISIONS = DERIVED / "replay" / "decisions" / "replay_decisions"
EXITS = {"4/10": "4/10", "4 then 10 or 12": "dynamic"}
# The owner's sittings: the first ended 30 Oct 2026 03:13 UTC, the second started 13:21 UTC
SECOND_SITTING_FROM = "2026-10-30T12:00:00Z"


def sample(saved_at: str, rnd: int) -> str:
    if saved_at < SECOND_SITTING_FROM:
        return "first sitting"
    return "second look (fixed chart)" if rnd == 2 else "new clips, second sitting"
TICK = 0.25


def fixed_exit(dt: DayTrades, t_in: float, fill: int, d: int, flat: float, stop_pts: float, tgt_pts: float) -> float:
    i, j = int(np.searchsorted(dt.t, t_in, "right")), int(np.searchsorted(dt.t, flat, "right"))
    px = dt.px[i:j]
    if not len(px):
        return 0.0
    s, t = round(stop_pts / TICK), round(tgt_pts / TICK)
    ks = np.flatnonzero((px - (fill - d * s)) * d <= 0)
    kt = np.flatnonzero((px - (fill + d * t)) * d >= 1)
    ks, kt = (ks[0] if len(ks) else len(px)), (kt[0] if len(kt) else len(px))
    if ks == kt == len(px):
        return float((px[-1] - fill) * d - SLIP_TICKS)
    return float(-s - SLIP_TICKS) if ks <= kt else float(t)


def all_exits(dt, t_in, price, d, flat) -> dict:
    fill = int(price) + d * SLIP_TICKS
    out = {k: exit_trade(dt, t_in, fill, d, flat, m)[0] * TICK for k, m in EXITS.items()}
    out["2/4"] = fixed_exit(dt, t_in, fill, d, flat, 2, 4) * TICK
    out["3/7"] = fixed_exit(dt, t_in, fill, d, flat, 3, 7) * TICK
    return out


def load_day(market: str, contract: str, day: str) -> DayTrades:
    if market == "ES":
        return DayTrades(contract, day)
    return DayTrades(trades=pd.read_parquet(DERIVED / "replay_trades" / f"{contract}_{day}.parquet"))


def main() -> None:
    s = load_settings()
    rng = np.random.default_rng(s["level_study"]["random_seed"])
    cost = CONTRACTS * s["backtest"]["commission_per_contract_round_trip"]
    dec = pd.DataFrame([json.load(open(f)) for f in sorted(glob.glob(str(DECISIONS / "*.json")))])
    ids = pd.read_csv(OUT / "replay_clip_ids.csv")
    tests = pd.read_csv(OUT / "replay_tests.csv")
    d = dec.merge(ids, left_on="clip_id", right_on="public_id").merge(tests, on="id")
    d = d[d["decision"].isin(["long", "short", "skip", "unsure"])]
    d = d.sort_values("round").drop_duplicates("id", keep="last")      # a second look replaces the first
    rows = []
    for (market, contract, day), g in d.groupby(["market", "contract", "day"]):
        dt, flat = load_day(market, contract, day), et(day, FLAT_ET)
        for r in g.itertuples():
            zd = int(r.direction)
            row = dict(clip=r.clip_id, decision=r.decision, sample=sample(r.saved_at, r.round), market=market, day=day,
                       tf=r.tf, zone_test=r.zone_test, zone_dir=zd)
            if r.decision in ("long", "short"):
                od = 1 if r.decision == "long" else -1
                row["with_zone"] = od == zd
                row.update({f"you {k}": v for k, v in all_exits(dt, r.entry_time, round(r.entry_price / TICK), od, flat).items()})
            k, _ = TestReader.turn_trade(dt, r.time, zd, int(r.edge))
            if k is not None:
                row.update({f"bot {x}": v for x, v in all_exits(dt, float(dt.t[k]), int(dt.px[k]), zd, flat).items()})
            for n in range(20):                                  # random entries in the decision window
                t0 = r.time + rng.uniform(0, 300)
                i = int(np.searchsorted(dt.t, t0, "right"))
                if i >= len(dt.t):
                    continue
                rd = int(rng.choice([-1, 1]))
                for x, v in all_exits(dt, float(dt.t[i]), int(dt.px[i]), rd, flat).items():
                    row.setdefault(f"random {x}", []).append(v)
            rows.append(row)
    x = pd.DataFrame(rows)
    for col in [c for c in x.columns if c.startswith("random ")]:
        x[col] = x[col].map(lambda v: float(np.mean(v)) if isinstance(v, list) else np.nan)
    x.to_csv(OUT / "replay_scores.csv", index=False)

    usd = lambda pts: pts * ES_POINT - cost  # noqa: E731
    out = []
    for smp in ["all", "first sitting", "second look (fixed chart)", "new clips, second sitting"]:
        y = x if smp == "all" else x[x["sample"] == smp]
        took = y["decision"].isin(["long", "short"])
        for ex in ["4/10", "4 then 10 or 12", "2/4", "3/7"]:
            def add(name, v):
                v = v.dropna()
                out.append(dict(sample=smp, exit=ex, group=name, trades=len(v),
                                win_rate=(v > 0).mean() if len(v) else np.nan, avg_points=v.mean() if len(v) else np.nan,
                                usd_per_trade=usd(v.mean()) if len(v) else np.nan,
                                net_usd=usd(v).sum() if len(v) else 0.0))
            add("your trades", y.loc[took, f"you {ex}"])
            add("bot entry, clips you traded", y.loc[took, f"bot {ex}"])
            add("bot entry, clips you skipped", y.loc[y["decision"] == "skip", f"bot {ex}"])
            add("bot entry, every clip you decided", y[f"bot {ex}"])
            add("random entries (average per clip), clips you traded", y.loc[took, f"random {ex}"])
            add("random entries (average per clip), all clips", y[f"random {ex}"])
    table = pd.DataFrame(out)
    table.to_csv(OUT / "replay_score_summary.csv", index=False)
    pd.set_option("display.width", 250)
    print(table[table["exit"].isin(["4/10", "4 then 10 or 12"])].round(2).to_string())


if __name__ == "__main__":
    main()
